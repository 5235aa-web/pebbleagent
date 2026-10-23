"""Token usage and cost reporting."""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# range -> days back from now; None means "no time bound".
_RANGES = {"session": None, "today": 0, "7d": 7, "30d": 30}


def _since_utc(rng: str):
    """Lower bound for a range, as a naive UTC string matching CURRENT_TIMESTAMP.

    created_at is written by SQLite in UTC while the user thinks in local time,
    so the local boundary is computed first and then converted. Comparing a
    local string directly would silently shift the window by the UTC offset.
    """
    if rng == "session":
        return None
    now = datetime.now().astimezone()
    if rng == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=_RANGES[rng])
    return start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _empty_stats():
    return {
        "total_cost": 0.0,
        "total_input_tokens": 0,
        "total_cache_hit_tokens": 0,
        "total_output_tokens": 0,
        "api_calls": 0,
        "currency": "CNY",
        "started_at": None,
        "ended_at": None,
        "models": [],
        "calls": [],
    }


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "usage.stats":
        rng = params.get("range", "session")
        if rng not in _RANGES:
            return {"id": rid, "error": {"code": -32602, "message": f"unknown range: {rng}"}}

        sid = params.get("session_id", "")
        try:
            limit = int(params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 500))

        from agent_core import agent_chat_history as history

        conversation_id = None
        if rng == "session":
            if not sid:
                return {"id": rid, "error": {"code": -32000, "message": "session_id required for range=session"}}
            try:
                conv = history.get_agent_conversation_by_uuid(sid)
            except Exception as e:
                logger.warning(f"[Usage] Failed to resolve conversation for {sid}: {e}")
                conv = None
            if not conv:
                return {"id": rid, "result": _empty_stats()}
            conversation_id = conv["id"]

        try:
            stats = history.get_usage_stats(
                conversation_id=conversation_id,
                since=_since_utc(rng),
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"[Usage] Failed to read usage stats: {e}", exc_info=True)
            return {"id": rid, "error": {"code": -32000, "message": f"failed to read usage: {e}"}}

        stats["range"] = rng
        stats["session_id"] = sid if rng == "session" else None
        stats["model"] = ctx.active_model
        return {"id": rid, "result": stats}

    return None
