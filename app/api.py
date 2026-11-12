
import logging
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
