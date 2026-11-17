
# Must run first: maps SAP BTP service bindings to environment variables
from app import cloud_env  # noqa: F401

import json
import logging
import os
import re
import threading
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import auth, system_info
from app.agent.agent import SAPAgent
from app.attachments import extract as attachment_extract
from app.attachments import reader as attachment_reader
from app.attachments import sensitive as attachment_sensitive
from app.modules import MODULES, get_module
from app.rag import knowledge_store
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

# Index new or changed shipped documents (SAP HANA Cloud Vector Engine on BTP, ChromaDB locally)
if os.getenv("KB_SYNC_ON_STARTUP", "1") != "0":
    knowledge_store.sync_bundled_in_background()


@app.exception_handler(knowledge_store.KnowledgeError)
def knowledge_error(request, error):
    return JSONResponse(status_code=error.status, content={"detail": str(error)})


@app.exception_handler(chat_store.StoreUnavailable)
def history_unavailable(request, error):
    """History database down (e.g. SAP HANA Cloud stopped): clear answer instead of a 500."""
    logger.warning("Chat history unavailable: %s", error)
    return JSONResponse(status_code=503, content={"detail": f"Chat history temporarily unavailable. {error}"})


def current_user(request: Request) -> str:
    """Signed-in user (validated XSUAA token on SAP BTP, "local" otherwise)."""
    return auth.current_user(request)


NOT_FOUND = "Conversation not found."


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


def check_session_owner(session_id: str, owner: str):
    """404 when the conversation id belongs to another user (same answer as an unknown id)."""
    with registry_lock:
        agent = agents.get(session_id)
    if agent is not None and agent.owner != owner:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    try:
        if not chat_store.session_available(session_id, owner):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    except chat_store.StoreUnavailable:
        # Cannot check the history: only an agent created by this user (checked above) or a new one
        pass


def get_session(session_id: str, owner: str, requested_module: str | None = None):
    """
    Return (agent, lock) for a session of this user, restoring its history if persisted.
    A conversation keeps the module it was created with.
    """

    check_session_owner(session_id, owner)

    with registry_lock:
        if session_id not in agents:
            try:
                conversation = chat_store.get_conversation(session_id, owner)
            except chat_store.StoreUnavailable as error:
                # Answer without the previous messages rather than not at all
                logger.warning("History of %s not restored: %s", session_id, error)
                conversation = None
            module_key = conversation["module"] if conversation else requested_module
            agent = SAPAgent(module=get_module(module_key), session_id=session_id, owner=owner)

            if conversation:
                agent.restore_history(conversation["messages"])

            agents[session_id] = agent
            session_locks[session_id] = threading.Lock()

        if agents[session_id].owner != owner:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return agents[session_id], session_locks[session_id]


def module_key(agent):
    return agent.module.key if agent.module else None


def message_attachments(session_id, owner, request):
    """Summaries of the documents sent with a message (only this user's documents of this conversation)."""
    if not request.attachments:
        return []
    try:
        return chat_store.list_attachments(session_id, owner, request.attachments)
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
def chat(request: ChatRequest, user: str = Depends(current_user)):

    session_id = request.session_id or str(uuid4())
    agent, lock = get_session(session_id, user, request.module)

    try:
        attachments = message_attachments(session_id, user, request)

        with lock:
            result = agent.ask_with_details(request.message + attachment_reader.note(attachments))

        save_exchange_safely(
            session_id=session_id,
            owner=user,
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
def chat_stream(request: ChatRequest, user: str = Depends(current_user)):
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
    agent, lock = get_session(session_id, user, request.module)
    attachments = message_attachments(session_id, user, request)

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
                        owner=user,
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
def list_sessions(user: str = Depends(current_user)):
    return {"sessions": chat_store.list_conversations(user)}


@app.get("/api/sessions/{session_id}")
def get_session_history(session_id: str, user: str = Depends(current_user)):

    conversation = chat_store.get_conversation(session_id, user)

    if conversation is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    return conversation


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, request: RenameRequest, user: str = Depends(current_user)):

    if not request.title.strip():
        raise HTTPException(status_code=422, detail="The title cannot be empty.")

    if not chat_store.rename_conversation(session_id, user, request.title):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    conversation = chat_store.get_conversation(session_id, user)
    return {"session_id": session_id, "title": conversation["title"]}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, user: str = Depends(current_user)):

    if not chat_store.delete_conversation(session_id, user):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    with registry_lock:
        agents.pop(session_id, None)
        session_locks.pop(session_id, None)

    return {"deleted": session_id}


@app.get("/api/knowledge-base")
def knowledge_base(user: str = Depends(current_user)):
    return system_info.get_knowledge_base(user)


# Workspaces whose agent searches the documentation (None: every workspace / general assistant)
KB_MODULES = {module.key for module in MODULES if "search_sap_documentation" in module.tools}


def _kb_module(module):
    module = (module or "").strip().lower() or None
    if module is not None and module not in KB_MODULES:
        raise HTTPException(status_code=422, detail="Unknown module.")
    return module


@app.post("/api/knowledge-base/documents")
def add_knowledge_document(file: UploadFile = File(...), module: str = Form(""),
                           user: str = Depends(current_user)):
    """Add a document to the knowledge base (text extracted, chunked and embedded)."""

    module = _kb_module(module)
    name = _clean_filename(file.filename)
    data = file.file.read(attachment_extract.MAX_FILE_BYTES + 1)
    try:
        text, _, _ = attachment_extract.extract_text(name, data)
    except attachment_extract.ExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return knowledge_store.add_document(name, text, module, user, len(data))


class AttachmentToKnowledge(BaseModel):
    session_id: str = Field(max_length=100)
    attachment_id: int
    module: str | None = Field(default=None, max_length=20)


@app.post("/api/knowledge-base/documents/from-attachment")
def add_knowledge_from_attachment(request: AttachmentToKnowledge, user: str = Depends(current_user)):
    """Add a document already attached to one of the user's conversations."""

    _check_session_id(request.session_id)
    module = _kb_module(request.module)
    found = chat_store.get_attachment_text(request.session_id, user, request.attachment_id)
    if not found:
        raise HTTPException(status_code=404, detail="Document not found.")
    summary, text = found
    return knowledge_store.add_document(summary["name"], text, module, user, summary["size_bytes"],
                                        masked_before=summary["masked"])


@app.delete("/api/knowledge-base/documents/{name}")
def delete_knowledge_document(name: str, user: str = Depends(current_user)):
    knowledge_store.delete_document(name, user)
    return {"deleted": name}


@app.get("/api/status")
def status(check_ai: bool = False, user: str = Depends(current_user)):
    return system_info.get_system_status(check_ai=check_ai, owner=user)


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
def upload_attachment(session_id: str, file: UploadFile = File(...), user: str = Depends(current_user)):
    """
    Attach a document to a conversation (which may not exist yet: the first
    message creates it). Only the extracted text is stored, never the file.
    """

    _check_session_id(session_id)
    check_session_owner(session_id, user)

    if len(chat_store.list_attachments(session_id, user)) >= MAX_ATTACHMENTS_PER_CONVERSATION:
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

    # Credentials and card numbers never reach the database nor the model
    text, masked = attachment_sensitive.mask(text, name, attachment_sensitive.PRIVATE)
    return chat_store.add_attachment(session_id, user, name, len(data), pages, text, truncated, masked)


@app.get("/api/sessions/{session_id}/attachments")
def list_session_attachments(session_id: str, user: str = Depends(current_user)):
    _check_session_id(session_id)
    return {"attachments": chat_store.list_attachments(session_id, user)}


@app.delete("/api/sessions/{session_id}/attachments/{attachment_id}")
def delete_session_attachment(session_id: str, attachment_id: int, user: str = Depends(current_user)):
    _check_session_id(session_id)
    if not chat_store.delete_attachment(session_id, user, attachment_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"deleted": attachment_id}
