"""
Persistent chat history for SAP AI Copilot: conversations, messages and
attached documents.

Stored in SAP HANA Cloud on SAP BTP (bound "hana" schema service) and in a
local SQLite file otherwise (data/chat_history.db), kept separate from the SAP
business data. See app.storage.database. The SQL below runs on both engines.

Every conversation and attached document belongs to one user ("owner": the SAP
login on BTP, "local" locally). Every function filters on it: a conversation of
another user behaves exactly like a conversation that does not exist.
"""

import json
import logging
import threading
from datetime import datetime, timezone

from app.storage.database import StoreUnavailable, get_database

logger = logging.getLogger("sap_ai_copilot.chat_store")

_initialized = False
_init_lock = threading.Lock()

TITLE_MAX_LENGTH = 60


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def initialize():
    """Create the tables. Never blocks the startup: retried on first use if the database is down."""
    try:
        _database()
    except StoreUnavailable as error:
        logger.warning("Chat history not available at startup: %s", error)


def reset():
    """Forget the initialisation (tests, database switch)."""
    global _initialized
    _initialized = False


def database():
    """Database of the history, tables created (shared with the knowledge base registry)."""
    return _database()


def _database():
    global _initialized
    database = get_database()
    if not _initialized:
        with _init_lock:
            if not _initialized:
                database.initialize()
                _initialized = True
    return database


def describe(owner: str):
    """Engine of the history and the user's conversation count, for the status page."""
    info = get_database().describe()
    try:
        with _database().cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS conversations FROM conversations WHERE owner = ?", (owner,))
            info["conversations"] = cursor.fetchone()["conversations"]
        info.update(available=True, error=None)
    except Exception as error:  # status page: never fails
        info.update(available=False, error=str(error) if isinstance(error, StoreUnavailable) else type(error).__name__)
    return info


def build_title(message: str) -> str:
    title = " ".join(message.split())

    if len(title) > TITLE_MAX_LENGTH:
        title = title[:TITLE_MAX_LENGTH - 1].rstrip() + "…"

    return title or "New conversation"


def session_available(session_id: str, owner: str) -> bool:
    """
    True when this user may use this conversation id: it is theirs, or it does not
    exist yet (new chat). False when it belongs to someone else.
    """
    with _database().cursor() as cursor:
        row = cursor.execute(
            "SELECT owner FROM conversations WHERE session_id = ?",
            (session_id,)
        ).fetchone()

    return row is None or row["owner"] == owner


def save_exchange(
    session_id: str,
    owner: str,
    user_message: str,
    assistant_message: str,
    sources: list,
    tools_used: list,
    module: str | None = None,
    tables: list | None = None,
    attachments: list | None = None
):
    """Store one user/assistant exchange, creating the conversation if needed. Returns the answer's message id."""

    now = _now()

    with _database().cursor() as cursor:
        # Update first, insert when the conversation is new (no engine-specific upsert)
        cursor.execute(
            "UPDATE conversations SET updated_at = ? WHERE session_id = ? AND owner = ?",
            (now, session_id, owner)
        )
        if cursor.rowcount == 0:
            # Fails on the primary key if the id belongs to another user: nothing is written
            cursor.execute(
                """
                INSERT INTO conversations (session_id, title, created_at, updated_at, module, owner)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, build_title(user_message), now, now, module, owner)
            )

        insert = """
            INSERT INTO messages
                (session_id, role, content, sources, tools_used, tables, attachments, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
        cursor.execute(insert, (session_id, "user", user_message, "[]", "[]", "[]",
                                json.dumps(attachments or [], ensure_ascii=False), now))
        cursor.execute(insert, (
            session_id,
            "assistant",
            assistant_message,
            json.dumps(sources, ensure_ascii=False),
            json.dumps(tools_used, ensure_ascii=False),
            json.dumps(tables or [], ensure_ascii=False),
            "[]",
            now
        ))
        # Id of the answer: the feedback buttons refer to it
        return int(cursor.last_id())


def list_conversations(owner: str):
    with _database().cursor() as cursor:
        rows = cursor.execute(
            """
            SELECT
                c.session_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.module,
                (SELECT COUNT(*) FROM messages m WHERE m.session_id = c.session_id) AS message_count
            FROM conversations c
            WHERE c.owner = ?
            ORDER BY c.updated_at DESC
            """,
            (owner,)
        ).fetchall()

    return rows


def get_conversation(session_id: str, owner: str):
    with _database().cursor() as cursor:
        conversation = cursor.execute(
            """
            SELECT session_id, title, created_at, updated_at, module
            FROM conversations
            WHERE session_id = ? AND owner = ?
            """,
            (session_id, owner)
        ).fetchone()

        if conversation is None:
            return None

        rows = cursor.execute(
            """
            SELECT m.message_id, m.role, m.content, m.sources, m.tools_used, m.tables, m.attachments,
                   m.created_at, f.rating, f.user_comment
            FROM messages m
            LEFT JOIN feedback f ON f.message_id = m.message_id
            WHERE m.session_id = ?
            ORDER BY m.message_id ASC
            """,
            (session_id,)
        ).fetchall()

    result = dict(conversation)
    result["messages"] = [
        {
            "role": row["role"],
            "content": row["content"],
            "sources": json.loads(row["sources"]),
            "tools_used": json.loads(row["tools_used"]),
            "tables": json.loads(row["tables"]),
            "attachments": json.loads(row["attachments"]),
            "created_at": row["created_at"],
            "id": int(row["message_id"]),
            # Feedback of the user on an answer, or None
            "feedback": {"rating": int(row["rating"]), "comment": row["user_comment"] or ""}
            if row["rating"] is not None else None
        }
        for row in rows
    ]

    return result


def rename_conversation(session_id: str, owner: str, title: str) -> bool:
    """Change a conversation title (normalised like generated titles)."""

    with _database().cursor() as cursor:
        cursor.execute(
            "UPDATE conversations SET title = ? WHERE session_id = ? AND owner = ?",
            (build_title(title), session_id, owner)
        )
        return cursor.rowcount > 0


def delete_conversation(session_id: str, owner: str) -> bool:
    with _database().cursor() as cursor:
        cursor.execute("DELETE FROM conversations WHERE session_id = ? AND owner = ?", (session_id, owner))
        if cursor.rowcount == 0:
            return False
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM attachments WHERE session_id = ? AND owner = ?", (session_id, owner))
        # The feedback copies the question and the answer: it goes with the conversation
        cursor.execute("DELETE FROM feedback WHERE session_id = ? AND owner = ?", (session_id, owner))
        return True


# ----------------------------------------------------------------------
# Attached documents
# ----------------------------------------------------------------------

SUMMARY_COLUMNS = "attachment_id, name, size_bytes, pages, chars, truncated, created_at, masked"


def _attachment_summary(row):
    return {
        "id": int(row["attachment_id"]),
        "name": row["name"],
        "size_bytes": row["size_bytes"],
        "pages": row["pages"],
        "chars": row["chars"],
        "truncated": bool(row["truncated"]),
        "created_at": row["created_at"],
        # Sensitive values masked at upload: {kind: count}
        "masked": json.loads(row["masked"]) if row.get("masked") else {}
    }


def add_attachment(session_id: str, owner: str, name: str, size_bytes: int, pages, text: str, truncated: bool,
                   masked: dict | None = None):
    """Store an attached document. `text` must already be masked (app.attachments.sensitive)."""
    with _database().cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO attachments
                (session_id, owner, name, size_bytes, pages, chars, text, truncated, created_at, masked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, owner, name, size_bytes, pages, len(text), text, int(truncated), _now(),
             json.dumps(masked or {}))
        )
        attachment_id = cursor.last_id()
        row = cursor.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM attachments WHERE attachment_id = ?",
            (attachment_id,)
        ).fetchone()

    return _attachment_summary(row)


def list_attachments(session_id: str, owner: str, ids=None):
    """Summaries of the documents of a conversation (optionally only these ids)."""

    with _database().cursor() as cursor:
        rows = cursor.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM attachments WHERE session_id = ? AND owner = ? ORDER BY attachment_id",
            (session_id, owner)
        ).fetchall()

    summaries = [_attachment_summary(row) for row in rows]
    if ids is not None:
        wanted = set(ids)
        summaries = [s for s in summaries if s["id"] in wanted]
    return summaries


def get_attachment_text(session_id: str, owner: str, attachment_id: int):
    """(summary, text) of a document of this conversation, or None."""

    with _database().cursor() as cursor:
        row = cursor.execute(
            f"SELECT {SUMMARY_COLUMNS}, text FROM attachments"
            " WHERE session_id = ? AND owner = ? AND attachment_id = ?",
            (session_id, owner, attachment_id)
        ).fetchone()

    return (_attachment_summary(row), row["text"]) if row else None


def mask_unchecked_attachments():
    """Mask the documents attached before sensitive data masking existed. Returns how many changed."""
    from app.attachments import sensitive

    changed = 0
    with _database().cursor() as cursor:
        rows = cursor.execute("SELECT attachment_id, name, text FROM attachments WHERE masked IS NULL").fetchall()
    for row in rows:
        text, masked = sensitive.mask(row["text"], row["name"], sensitive.PRIVATE)
        with _database().cursor() as cursor:
            cursor.execute(
                "UPDATE attachments SET text = ?, chars = ?, masked = ? WHERE attachment_id = ?",
                (text, len(text), json.dumps(masked), row["attachment_id"])
            )
        changed += text != row["text"]
    return changed


def delete_attachment(session_id: str, owner: str, attachment_id: int) -> bool:
    with _database().cursor() as cursor:
        cursor.execute(
            "DELETE FROM attachments WHERE session_id = ? AND owner = ? AND attachment_id = ?",
            (session_id, owner, attachment_id)
        )
        return cursor.rowcount > 0
