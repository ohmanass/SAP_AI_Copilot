
# Must run first: maps SAP BTP service bindings to environment variables
from app import cloud_env  # noqa: F401

import json
import logging
import os
import re
import threading
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app import auth, system_info, web
from app.agent import followups, titles
from app.agent.agent import SAPAgent
from app.attachments import extract as attachment_extract
from app.attachments import reader as attachment_reader
from app.attachments import sensitive as attachment_sensitive
from app.i18n import languages as ui_languages
from app.i18n import translator as ui_translator
from app.modules import MODULES, get_module
from app.rag import knowledge_store
from app.storage import chat_store, feedback_store


logger = logging.getLogger("sap_ai_copilot.api")

app = FastAPI(
    title="SAP AI Copilot API",
    description="Backend API for SAP AI Copilot",
    version="1.1.0"
)

# On SAP BTP: every /api call (except /api/health) needs a valid XSUAA token
auth.install(app)

chat_store.initialize()

# Index new or changed shipped documents (SAP HANA Cloud Vector Engine on BTP, ChromaDB locally)
if os.getenv("KB_SYNC_ON_STARTUP", "1") != "0":
    knowledge_store.sync_bundled_in_background()

# Title the conversations stored before titles were managed by the model
if os.getenv("TITLE_BACKFILL_ON_STARTUP", "1") != "0":
    titles.backfill_in_background()


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
    """The answer is shown even if the history cannot be saved. Returns the answer's message id or None."""
    try:
        return chat_store.save_exchange(**exchange)
    except chat_store.StoreUnavailable as error:
        logger.warning("Exchange not saved, chat history unavailable: %s", error)
        return None

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
    # Id of the stored answer (feedback); None when the history is unavailable
    message_id: int | None = None
    # Title written by the model for a new conversation (None: provisional title kept)
    title: str | None = None
    # Follow-up questions suggested by the model (may be empty)
    suggestions: list[str] = []


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


def plan_title(session_id, owner):
    """
    After a saved exchange: the title to write (first exchange) or to review
    (exchanges 2, 4, 8, 16), started in the background. (Future, title before) or None.
    """
    try:
        state = chat_store.title_state(session_id, owner)
        if not state or not titles.due(state["exchanges"], state["source"]):
            return None
        exchanges = chat_store.recent_exchanges(session_id, owner, titles.EXCHANGES_SHOWN)
    except chat_store.StoreUnavailable:
        return None
    current = state["title"] if state["source"] == "model" else None
    return titles.start(exchanges, current), state["title"]


def start_followups(agent, request, answer, tables=None):
    """Follow-up questions for this answer, limited to the tools of the agent's workspace (Future)."""
    workspace = f"{agent.module.code} - {agent.module.name}" if agent.module else ""
    return followups.start(request.message, answer, agent.tools, workspace, tables)


def save_title(planned, session_id, owner):
    """Store the title of plan_title when it changed (never over a user's title); returns it, or None."""
    if not planned:
        return None
    future, before = planned
    title = titles.result(future)
    if not title or chat_store.build_title(title) == before:
        return None
    try:
        if chat_store.set_generated_title(session_id, owner, title):
            return chat_store.build_title(title)
    except chat_store.StoreUnavailable as error:
        logger.warning("Generated title not saved: %s", error)
    return None


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
        followups_future = start_followups(agent, request, result["response"], result["tables"])

        message_id = save_exchange_safely(
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
            tables=result["tables"],
            message_id=message_id,
            title=save_title(plan_title(session_id, user), session_id, user) if message_id else None,
            suggestions=followups.result(followups_future)
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
      done         {"type", "session_id", "response", "sources", "tools_used", "tables", "message_id"}
      title        {"type", "session_id", "title"}   title written or changed by the model (after "done")
      suggestions  {"type", "message_id", "items"}   follow-up questions for this answer (after "done")
      error        {"type", "message"}
    """

    session_id = request.session_id or str(uuid4())
    agent, lock = get_session(session_id, user, request.module)
    attachments = message_attachments(session_id, user, request)

    def event_line(event):
        return json.dumps(event, ensure_ascii=False) + "\n"

    def generate():
        yield event_line({"type": "session", "session_id": session_id, "module": module_key(agent)})
        saved = False
        answer = None

        with lock:
            try:
                for event in agent.ask_stream(request.message + attachment_reader.note(attachments)):

                    if event["type"] != "done":
                        yield event_line(event)
                        continue

                    message_id = save_exchange_safely(
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
                    saved = message_id is not None
                    answer = (event["response"], message_id, event["tables"])
                    yield event_line({**event, "session_id": session_id, "module": module_key(agent),
                                      "message_id": message_id})

            except Exception:
                logger.exception("Streaming chat request failed")
                yield event_line({
                    "type": "error",
                    "message": "An error occurred while processing your request."
                })

        # After the answer, outside the conversation lock: the answer is already on screen
        if answer is None:
            return
        followups_future = start_followups(agent, request, answer[0], answer[2])
        planned_title = plan_title(session_id, user) if saved else None
        title = save_title(planned_title, session_id, user)
        if title:
            yield event_line({"type": "title", "session_id": session_id, "title": title})
        suggestions = followups.result(followups_future)
        if suggestions:
            yield event_line({"type": "suggestions", "message_id": answer[1], "items": suggestions})

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


@app.post("/api/sessions/{session_id}/title")
def automatic_title(session_id: str, user: str = Depends(current_user)):
    """The model writes the title now and manages it again (after a rename by the user)."""

    exchanges = chat_store.recent_exchanges(session_id, user, titles.EXCHANGES_SHOWN)
    if not exchanges:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    title = titles.generate(exchanges)
    if not title:
        raise HTTPException(status_code=503, detail="The title could not be written. Try again later.")
    chat_store.set_generated_title(session_id, user, title, force=True)
    return {"session_id": session_id, "title": chat_store.build_title(title)}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, user: str = Depends(current_user)):

    if not chat_store.delete_conversation(session_id, user):
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    with registry_lock:
        agents.pop(session_id, None)
        session_locks.pop(session_id, None)

    return {"deleted": session_id}


# ----------------------------------------------------------------------
# Feedback on answers
# ----------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    rating: int = Field(description="1 (useful) or -1 (not useful)")
    comment: str = Field(default="", max_length=feedback_store.COMMENT_MAX_LENGTH)


@app.put("/api/sessions/{session_id}/messages/{message_id}/feedback")
def set_message_feedback(session_id: str, message_id: int, request: FeedbackRequest,
                         user: str = Depends(current_user)):
    if request.rating not in (1, -1):
        raise HTTPException(status_code=422, detail="The rating must be 1 or -1.")
    feedback = feedback_store.set_feedback(session_id, user, message_id, request.rating, request.comment)
    if feedback is None:
        raise HTTPException(status_code=404, detail="Answer not found.")
    return feedback


@app.delete("/api/sessions/{session_id}/messages/{message_id}/feedback")
def delete_message_feedback(session_id: str, message_id: int, user: str = Depends(current_user)):
    if not feedback_store.delete_feedback(session_id, user, message_id):
        raise HTTPException(status_code=404, detail="Feedback not found.")
    return {"deleted": message_id}


@app.get("/api/feedback")
def list_feedback(request: Request, rating: int | None = None, user: str = Depends(current_user)):
    """Feedback review: every user's feedback for a reviewer, the user's own otherwise."""
    if rating not in (None, 1, -1):
        raise HTTPException(status_code=422, detail="The rating must be 1 or -1.")
    return feedback_store.review(user, auth.is_reviewer(request), rating)


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
    document = knowledge_store.add_document(name, text, module, user, len(data))
    return dict(document, transcribed=attachment_extract.transcribed_parts(text))


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


# ----------------------------------------------------------------------
# Interface languages
# ----------------------------------------------------------------------

@app.get("/api/i18n/languages")
def interface_languages():
    """Reviewed languages (frontend files) and languages translated by the AI model."""
    return ui_languages.catalogue()


@app.get("/api/i18n/{code}.properties")
def interface_translation(code: str, request: Request, user: str = Depends(current_user)):
    """
    SAPUI5 resource bundle of a language translated by the AI model (translated
    once, then cached for every user). ETag: the browser reloads it only when it changed.
    """
    if not ui_languages.is_machine(code):
        raise HTTPException(status_code=404, detail="No translation by the AI model for this language.")
    try:
        content = ui_translator.bundle_file(code)
    except ui_translator.TranslationUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    etag = '"' + ui_translator._hash(content)[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content, media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/api/dashboard")
def dashboard(refresh: bool = False, user: str = Depends(current_user)):
    """Live SAP indicators of the home screen (cached 5 minutes)."""
    from app import dashboard as home_dashboard
    return home_dashboard.indicators(refresh)


@app.get("/api/usage")
def ai_usage(days: int = 30, user: str = Depends(current_user)):
    """Calls and tokens sent to SAP Generative AI Hub (counters only, every user)."""
    from app.llm import usage
    return usage.report(days)


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
    summary = chat_store.add_attachment(session_id, user, name, len(data), pages, text, truncated, masked)
    # Images and scanned pages read by the AI model: the user is told to check the values
    return dict(summary, transcribed=attachment_extract.transcribed_parts(text))


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


# Local development: the interface on the same server and port as the API (app/web.py).
# Last: the API routes above take precedence over the files of the interface.
web.mount_frontend(app)
