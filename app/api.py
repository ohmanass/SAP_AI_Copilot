
# Must run first: maps SAP BTP service bindings to environment variables
from app import cloud_env  # noqa: F401

import json
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import system_info
from app.agent.agent import SAPAgent
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

chat_store.initialize()

# One agent per conversation (rehydrated from chat history after a restart)
agents = {}
# One lock per conversation so concurrent requests don't interleave messages
session_locks = {}
registry_lock = threading.Lock()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=100)


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


def get_session(session_id: str):
    """Return (agent, lock) for a session, restoring its history if persisted."""

    with registry_lock:
        if session_id not in agents:
            agent = SAPAgent()

            conversation = chat_store.get_conversation(session_id)
            if conversation:
                agent.restore_history(conversation["messages"])

            agents[session_id] = agent
            session_locks[session_id] = threading.Lock()

        return agents[session_id], session_locks[session_id]


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
        agent, lock = get_session(session_id)

        with lock:
            result = agent.ask_with_details(request.message)

        chat_store.save_exchange(
            session_id=session_id,
            user_message=request.message,
            assistant_message=result["response"],
            sources=result["sources"],
            tools_used=result["tools_used"]
        )

        return ChatResponse(
            session_id=session_id,
            response=result["response"],
            sources=result["sources"],
            tools_used=result["tools_used"]
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
      done         {"type", "session_id", "response", "sources", "tools_used"}
      error        {"type", "message"}
    """

    session_id = request.session_id or str(uuid4())
    agent, lock = get_session(session_id)

    def event_line(event):
        return json.dumps(event, ensure_ascii=False) + "\n"

    def generate():
        yield event_line({"type": "session", "session_id": session_id})

        with lock:
            try:
                for event in agent.ask_stream(request.message):

                    if event["type"] != "done":
                        yield event_line(event)
                        continue

                    chat_store.save_exchange(
                        session_id=session_id,
                        user_message=request.message,
                        assistant_message=event["response"],
                        sources=event["sources"],
                        tools_used=event["tools_used"]
                    )
                    yield event_line({**event, "session_id": session_id})

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


@app.get("/api/sessions")
def list_sessions():
    return {"sessions": chat_store.list_conversations()}


@app.get("/api/sessions/{session_id}")
def get_session_history(session_id: str):

    conversation = chat_store.get_conversation(session_id)

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return conversation


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
