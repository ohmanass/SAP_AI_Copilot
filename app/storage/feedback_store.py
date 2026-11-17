"""
Feedback on the assistant's answers (👍 / 👎 and an optional comment).

One feedback per answer, given by the owner of the conversation. The question
and the answer are copied with it, so the review page can list them without
reading the conversations; the feedback is deleted with its conversation.

Review: a user sees their own feedback; a reviewer (FeedbackReview scope on
SAP BTP, everyone locally) sees the feedback of every user. Statistics are
computed on what the caller may see.
"""

import json

from app.attachments import sensitive
from app.storage import chat_store

COMMENT_MAX_LENGTH = 1000
REVIEW_LIMIT = 200


def set_feedback(session_id: str, owner: str, message_id: int, rating: int, comment: str = ""):
    """
    Store (or replace) the feedback on an answer of the user's conversation.
    Returns the feedback, or None when the answer is not one of the user's.
    """

    # A comment is free text: credentials pasted in it are masked like in documents
    comment, _ = sensitive.mask((comment or "").strip()[:COMMENT_MAX_LENGTH], "", sensitive.PRIVATE)
    now = chat_store._now()

    with chat_store.database().cursor() as cursor:
        answer = cursor.execute(
            """
            SELECT m.message_id, m.content, m.tools_used, c.module
            FROM messages m
            JOIN conversations c ON c.session_id = m.session_id
            WHERE m.message_id = ? AND m.session_id = ? AND c.owner = ? AND m.role = 'assistant'
            """,
            (message_id, session_id, owner)
        ).fetchone()
        if answer is None:
            return None

        question = cursor.execute(
            """
            SELECT content FROM messages
            WHERE session_id = ? AND role = 'user' AND message_id < ?
            ORDER BY message_id DESC
            LIMIT 1
            """,
            (session_id, message_id)
        ).fetchone()

        cursor.execute(
            "UPDATE feedback SET rating = ?, user_comment = ?, updated_at = ? WHERE message_id = ? AND owner = ?",
            (rating, comment, now, message_id, owner)
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO feedback (message_id, session_id, owner, rating, user_comment, module,
                                      question, answer, tools_used, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, owner, rating, comment, answer["module"],
                 question["content"] if question else "", answer["content"], answer["tools_used"], now, now)
            )

    return {"message_id": message_id, "rating": rating, "comment": comment}


def delete_feedback(session_id: str, owner: str, message_id: int) -> bool:
    with chat_store.database().cursor() as cursor:
        cursor.execute(
            "DELETE FROM feedback WHERE message_id = ? AND session_id = ? AND owner = ?",
            (message_id, session_id, owner)
        )
        return cursor.rowcount > 0


def review(owner: str, reviewer: bool, rating: int | None = None):
    """Statistics and latest feedback (all users for a reviewer, the user's own otherwise)."""

    scope_sql, scope_params = ("", []) if reviewer else (" WHERE owner = ?", [owner])

    with chat_store.database().cursor() as cursor:
        counts = {int(row["rating"]): int(row["total"]) for row in cursor.execute(
            f"SELECT rating, COUNT(*) AS total FROM feedback{scope_sql} GROUP BY rating", scope_params
        ).fetchall()}

        conditions, params = [], []
        if not reviewer:
            conditions.append("owner = ?")
            params.append(owner)
        if rating is not None:
            conditions.append("rating = ?")
            params.append(rating)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = cursor.execute(
            f"""
            SELECT message_id, session_id, owner, rating, user_comment, module, question, answer,
                   tools_used, created_at, updated_at
            FROM feedback{where}
            ORDER BY updated_at DESC
            LIMIT {REVIEW_LIMIT}
            """,
            params
        ).fetchall()

    positive, negative = counts.get(1, 0), counts.get(-1, 0)
    total = positive + negative
    return {
        "scope": "all" if reviewer else "mine",
        "stats": {
            "total": total,
            "positive": positive,
            "negative": negative,
            "satisfaction": round(100 * positive / total) if total else None
        },
        "items": [
            {
                "message_id": int(row["message_id"]),
                "session_id": row["session_id"],
                "rating": int(row["rating"]),
                "comment": row["user_comment"] or "",
                "module": row["module"],
                "question": row["question"],
                "answer": row["answer"],
                "tools_used": json.loads(row["tools_used"] or "[]"),
                "updated_at": row["updated_at"],
                # The conversation can only be opened by its owner
                "own": row["owner"] == owner,
                "user": row["owner"] if reviewer else None
            }
            for row in rows
        ]
    }
