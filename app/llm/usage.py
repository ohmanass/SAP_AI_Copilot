"""
AI usage tracking: calls and tokens sent to SAP Generative AI Hub.

Every model call of the app goes through this module (chat, titles,
follow-up suggestions, vision, embeddings, status check). The tokens reported
by the model are added to one row per day, feature and model (table ai_usage):
the table stays small and holds no content, only counters.

Recording never slows down nor breaks a call: the counters are written by a
background thread, and a database error only loses that count (logged).

An estimated cost is shown only when prices are configured (USAGE_PRICES, see
README): SAP AI Core bills in capacity units that depend on the contract, so
no price is assumed.
"""

import json
import logging
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("sap_ai_copilot.usage")

_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="usage")
_pending = []
_pending_lock = threading.Lock()


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _tokens(usage):
    if usage is None:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


def record(feature: str, model: str, prompt_tokens: int, completion_tokens: int = 0):
    """Count one model call (written in the background)."""
    entry = (_today(), feature, model or "unknown", int(prompt_tokens or 0), int(completion_tokens or 0))
    with _pending_lock:
        _pending.append(entry)
    try:
        _writer.submit(_flush)
    except RuntimeError:        # interpreter shutting down
        pass


def _flush():
    """Write the pending counts, merged per (day, feature, model)."""
    with _pending_lock:
        entries = list(_pending)
        _pending.clear()
    if not entries:
        return

    totals = defaultdict(lambda: [0, 0, 0])
    for day, feature, model, prompt, completion in entries:
        total = totals[(day, feature, model)]
        total[0] += 1
        total[1] += prompt
        total[2] += completion

    from app.storage import chat_store

    try:
        with chat_store.database().cursor() as cursor:
            for (day, feature, model), (calls, prompt, completion) in totals.items():
                cursor.execute(
                    """
                    UPDATE ai_usage
                    SET calls = calls + ?, prompt_tokens = prompt_tokens + ?, completion_tokens = completion_tokens + ?
                    WHERE usage_day = ? AND feature = ? AND model = ?
                    """,
                    (calls, prompt, completion, day, feature, model)
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        """
                        INSERT INTO ai_usage (usage_day, feature, model, calls, prompt_tokens, completion_tokens)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (day, feature, model, calls, prompt, completion)
                    )
    except Exception as error:
        logger.warning("AI usage not recorded (%s calls lost): %s", len(entries), error)


def flush_now():
    """Wait for the background writes (tests, shutdown)."""
    _writer.submit(_flush).result(timeout=30)


# ----------------------------------------------------------------------
# Model calls
# ----------------------------------------------------------------------

def chat_completion(feature: str, **parameters):
    """
    chat.completions.create of SAP Generative AI Hub, counted under `feature`.
    With stream=True, returns an iterator of chunks; the usage is read from the
    last chunk (stream_options include_usage) and counted when the stream ends.
    """
    from gen_ai_hub.proxy.native.openai import chat

    model = parameters.get("model_name") or "unknown"
    if parameters.get("stream"):
        parameters.setdefault("stream_options", {"include_usage": True})
        return _counted_stream(feature, model, chat.completions.create(**parameters))

    response = chat.completions.create(**parameters)
    # The requested model name (the response gives a dated version: one name per model in the report)
    record(feature, model, *_tokens(getattr(response, "usage", None)))
    return response


def _counted_stream(feature, model, stream):
    usage = None
    try:
        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            yield chunk
    finally:
        # Counted even when the user stops the answer (tokens are then unknown: 0)
        record(feature, model, *_tokens(usage))


def embeddings(feature: str, **parameters):
    """embeddings.create of SAP Generative AI Hub, counted under `feature`."""
    from gen_ai_hub.proxy.native.openai import embeddings as client

    response = client.create(**parameters)
    record(feature, parameters.get("model_name") or "unknown", *_tokens(getattr(response, "usage", None)))
    return response


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------

def prices():
    """
    Optional prices per million tokens, from USAGE_PRICES:
    {"currency": "EUR", "gpt-4o-mini": [input, output], "text-embedding-3-large": [input]}
    """
    try:
        configured = json.loads(os.getenv("USAGE_PRICES", "") or "{}")
    except ValueError:
        logger.warning("USAGE_PRICES is not valid JSON: no cost estimate")
        return None
    return configured if isinstance(configured, dict) and len(configured) > 1 else None


def _price_of(model, price_table):
    """Price entry of a model: exact name, or the configured name it starts with (versions)."""
    if not price_table:
        return None
    for name, value in price_table.items():
        if name != "currency" and (model == name or model.startswith(f"{name}-")):
            return value
    return None


def _cost(model, prompt, completion, price_table):
    price = _price_of(model, price_table)
    if not isinstance(price, list) or not price:
        return None
    input_price = float(price[0])
    output_price = float(price[1]) if len(price) > 1 else 0.0
    return (prompt * input_price + completion * output_price) / 1_000_000


def report(days: int = 30):
    """Usage of the last `days` days: totals, per feature, per model and per day."""

    days = max(1, min(int(days), 365))
    first_day = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    try:
        flush_now()
    except Exception:
        pass

    from app.storage import chat_store

    with chat_store.database().cursor() as cursor:
        rows = cursor.execute(
            """
            SELECT usage_day, feature, model, calls, prompt_tokens, completion_tokens
            FROM ai_usage WHERE usage_day >= ? ORDER BY usage_day
            """,
            (first_day,)
        ).fetchall()

    price_table = prices()

    def empty():
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0 if price_table else None}

    total = empty()
    by_feature, by_model, by_day = defaultdict(empty), defaultdict(empty), defaultdict(empty)
    for row in rows:
        calls, prompt, completion = int(row["calls"]), int(row["prompt_tokens"]), int(row["completion_tokens"])
        cost = _cost(row["model"], prompt, completion, price_table)
        for bucket in (total, by_feature[row["feature"]], by_model[row["model"]], by_day[row["usage_day"]]):
            bucket["calls"] += calls
            bucket["prompt_tokens"] += prompt
            bucket["completion_tokens"] += completion
            if bucket["cost"] is not None and cost is not None:
                bucket["cost"] += cost

    def listing(groups, key):
        return [dict(values, **{key: name}, total_tokens=values["prompt_tokens"] + values["completion_tokens"])
                for name, values in groups.items()]

    features = sorted(listing(by_feature, "feature"), key=lambda f: -f["total_tokens"])
    daily = []
    for offset in range(days):
        day = (datetime.now(timezone.utc).date() - timedelta(days=days - 1 - offset)).isoformat()
        values = by_day.get(day) or empty()
        daily.append(dict(values, day=day, total_tokens=values["prompt_tokens"] + values["completion_tokens"]))

    return {
        "days": days,
        "since": first_day,
        "currency": (price_table or {}).get("currency"),
        "total": dict(total, total_tokens=total["prompt_tokens"] + total["completion_tokens"]),
        "by_feature": features,
        "by_model": sorted(listing(by_model, "model"), key=lambda m: -m["total_tokens"]),
        "by_day": daily
    }
