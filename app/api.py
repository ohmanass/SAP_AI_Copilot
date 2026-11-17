
# Must run first: maps SAP BTP service bindings to environment variables
from app import cloud_env  # noqa: F401

import json
import logging
import re
import threading
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import auth, system_info
from app.agent.agent import SAPAgent
from app.attachments import extract as attachment_extract
from app.attachments import reader as attachment_reader
from app.modules import get_module
from app.storage import chat_store


logger = logging.getLogger("sap_ai_copilot.api")

app = FastAPI(
    title="SAP AI Copilot API",
    description="Backend API for SAP AI Copilot",
    version="1.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# On SAP BTP: every /api call (except /api/health) needs a valid XSUAA token
auth.install(app)

chat_store.initialize()


@app.exception_handler(chat_store.StoreUnavailable)
def history_unavailable(request, error):
    """History database down (e.g. SAP HANA Cloud stopped): clear answer instead of a 500."""
    logger.warning("Chat history unavailable: %s", error)
    return JSONResponse(status_code=503, content={"detail": f"Chat history temporarily unavailable. {error}"})


def save_exchange_safely(**exchange):
    """The answer is shown even if the history cannot be saved."""
    try:
        chat_store.save_exchange(**exchange)
    except chat_store.StoreUnavailable as error:
        logger.warning("Exchange not saved, chat history unavailable: %s", error)

# One agent per conversation (rehydrated from chat history after a restart)
agents = {}
# One lock per conversation so concurrent requests don't interleave messages
session_locks = {}
registry_lock = threading.Lock()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=100)
    # Module workspace for a new conversation (ignored for an existing one)
    module: str | None = Field(default=None, max_length=20)
    # Ids of documents uploaded to this conversation and sent with the message
    attachments: list[int] = Field(default_factory=list, max_length=10)


class Source(BaseModel):
    source: str
    chunk: int | None = None
    distance: float | None = None
    content: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    # Additive fields: existing clients can ignore them
    sources: list[Source] = []
    tools_used: list[str] = []
    module: str | None = None
    # SAP data tables built from the tool results (see app.agent.data_tables)
    tables: list[dict] = []


def get_session(session_id: str, requested_module: str | None = None):
    """
    Return (agent, lock) for a session, restoring its history if persisted.
    A conversation keeps the module it was created with.
    """

    with registry_lock:
        if session_id not in agents:
            try:
                conversation = chat_store.get_conversation(session_id)
            except chat_store.StoreUnavailable as error:
                # Answer without the previous messages rather than not at all
                logger.warning("History of %s not restored: %s", session_id, error)
                conversation = None
            module_key = conversation["module"] if conversation else requested_module
            agent = SAPAgent(module=get_module(module_key), session_id=session_id)

            if conversation:
                agent.restore_history(conversation["messages"])

            agents[session_id] = agent
            session_locks[session_id] = threading.Lock()

        return agents[session_id], session_locks[session_id]


def module_key(agent):
    return agent.module.key if agent.module else None


def message_attachments(session_id, request):
    """Summaries of the documents sent with a message (only this conversation's documents)."""
    if not request.attachments:
        return []
    try:
        return chat_store.list_attachments(session_id, request.attachments)
    except chat_store.StoreUnavailable as error:
        logger.warning("Attached documents unavailable: %s", error)
        return []


@app.get("/api/health")
def health():
    return {
        "status": "online",
        "application": "SAP AI Copilot"
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):

    session_id = request.session_id or str(uuid4())

    try:
        agent, lock = get_session(session_id, request.module)
        attachments = message_attachments(session_id, request)

        with lock:
            result = agent.ask_with_details(request.message + attachment_reader.note(attachments))

        save_exchange_safely(
            session_id=session_id,
            user_message=request.message,
            assistant_message=result["response"],
            sources=result["sources"],
            tools_used=result["tools_used"],
            module=module_key(agent),
            tables=result["tables"],
            attachments=attachments
        )

        return ChatResponse(
            session_id=session_id,
            response=result["response"],
            sources=result["sources"],
            tools_used=result["tools_used"],
            module=module_key(agent),
            tables=result["tables"]
        )

    except Exception as error:
        logger.exception("Chat request failed")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing your request."
        ) from error


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest):
    """
    Streaming variant of /api/chat. Response: NDJSON, one event per line:
      session      {"type", "session_id"}
      tool_start   {"type", "name"}
      tool_end     {"type", "name", "success"}
      token        {"type", "content"}
      done         {"type", "session_id", "response", "sources", "tools_used", "tables"}
      error        {"type", "message"}
    """

    session_id = request.session_id or str(uuid4())
    agent, lock = get_session(session_id, request.module)
    attachments = message_attachments(session_id, request)

    def event_line(event):
        return json.dumps(event, ensure_ascii=False) + "\n"

    def generate():
        yield event_line({"type": "session", "session_id": session_id, "module": module_key(agent)})

        with lock:
            try:
                for event in agent.ask_stream(request.message + attachment_reader.note(attachments)):

                    if event["type"] != "done":
                        yield event_line(event)
                        continue

                    save_exchange_safely(
                        session_id=session_id,
                        user_message=request.message,
                        assistant_message=event["response"],
                        sources=event["sources"],
                        tools_used=event["tools_used"],
                        module=module_key(agent),
                        tables=event["tables"],
                        attachments=attachments
                    )
                    yield event_line({**event, "session_id": session_id, "module": module_key(agent)})

            except Exception:
                logger.exception("Streaming chat request failed")
                yield event_line({
                    "type": "error",
                    "message": "An error occurred while processing your request."
                })

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/api/modules")
def list_modules():
    return {"modules": system_info.get_modules()}


@app.get("/api/sessions")
def list_sessions():
    return {"sessions": chat_store.list_conversations()}


@app.get("/api/sessions/{session_id}")
def get_session_history(session_id: str):

    conversation = chat_store.get_conversation(session_id)

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return conversation


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, request: RenameRequest):

    if not request.title.strip():
        raise HTTPException(status_code=422, detail="The title cannot be empty.")

    if not chat_store.rename_conversation(session_id, request.title):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conversation = chat_store.get_conversation(session_id)
    return {"session_id": session_id, "title": conversation["title"]}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):

    if not chat_store.delete_conversation(session_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    with registry_lock:
        agents.pop(session_id, None)
        session_locks.pop(session_id, None)

    return {"deleted": session_id}


@app.get("/api/knowledge-base")
def knowledge_base():
    return system_info.get_knowledge_base()


@app.get("/api/status")
def status(check_ai: bool = False):
    return system_info.get_system_status(check_ai=check_ai)


# ----------------------------------------------------------------------
# Attached documents
# ----------------------------------------------------------------------

# Conversation ids are generated by the backend or by the frontend (UUID)
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,100}$")
MAX_ATTACHMENTS_PER_CONVERSATION = 20


def _check_session_id(session_id: str):
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(status_code=422, detail="Invalid conversation id.")


def _clean_filename(name: str) -> str:
    name = re.split(r"[\\/]", name or "")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "document"


@app.post("/api/sessions/{session_id}/attachments")
def upload_attachment(session_id: str, file: UploadFile = File(...)):
    """
    Attach a document to a conversation (which may not exist yet: the first
    message creates it). Only the extracted text is stored, never the file.
    """

    _check_session_id(session_id)

    if len(chat_store.list_attachments(session_id)) >= MAX_ATTACHMENTS_PER_CONVERSATION:
        raise HTTPException(
            status_code=409,
            detail=f"A conversation can hold at most {MAX_ATTACHMENTS_PER_CONVERSATION} documents."
        )

    name = _clean_filename(file.filename)
    # Read one byte more than the limit to detect oversized files without loading them fully
    data = file.file.read(attachment_extract.MAX_FILE_BYTES + 1)

    try:
        text, pages, truncated = attachment_extract.extract_text(name, data)
    except attachment_extract.ExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return chat_store.add_attachment(session_id, name, len(data), pages, text, truncated)


@app.get("/api/sessions/{session_id}/attachments")
def list_session_attachments(session_id: str):
    _check_session_id(session_id)
    return {"attachments": chat_store.list_attachments(session_id)}


@app.delete("/api/sessions/{session_id}/attachments/{attachment_id}")
def delete_session_attachment(session_id: str, attachment_id: int):
    _check_session_id(session_id)
    if not chat_store.delete_attachment(session_id, attachment_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"deleted": attachment_id}
