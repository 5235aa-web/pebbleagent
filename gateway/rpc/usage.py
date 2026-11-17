"""Token usage and cost reporting."""

import json
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


def _pricing_currency_and_rate(srv) -> tuple:
    from agent_core.chat.usage import DEFAULT_CNY_PER_USD, normalize_currency, resolve_cny_per_usd
    for p in (srv._DATA_DIR / "model_pricing.json", srv._PROJECT_ROOT / "model_pricing.json"):
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return normalize_currency(data.get("default_currency"), "CNY"), resolve_cny_per_usd(data)
            except Exception:
                continue
    return "CNY", DEFAULT_CNY_PER_USD


def _empty_stats(currency: str = "CNY", cny_per_usd: float = 6.67):
    return {
        "total_cost": 0.0,
        "total_input_tokens": 0,
        "total_cache_hit_tokens": 0,
        "total_output_tokens": 0,
        "api_calls": 0,
        "currency": currency,
        "cny_per_usd": cny_per_usd,
        "cost_by_currency": {},
        "started_at": None,
        "ended_at": None,
        "models": [],
        "calls": [],
    }


def handle(ctx, method):
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
        base_cur, rate = _pricing_currency_and_rate(ctx.srv)

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
                return {"id": rid, "result": _empty_stats(base_cur, rate)}
            conversation_id = conv["id"]

        try:
            stats = history.get_usage_stats(
                conversation_id=conversation_id,
                since=_since_utc(rng),
                limit=limit,
                target_currency=base_cur,
                cny_per_usd=rate,
            )
        except Exception as e:
            logger.warning(f"[Usage] Failed to read usage stats: {e}", exc_info=True)
            return {"id": rid, "error": {"code": -32000, "message": f"failed to read usage: {e}"}}

        stats["range"] = rng
        stats["session_id"] = sid if rng == "session" else None
        stats["model"] = ctx.active_model
        # The window size otherwise only rides on `message.usage`, so a freshly
        # loaded frontend has no denominator for its context ring until the next
        # prompt produces one.
        try:
            from agent_core.agent_chat_service_v2 import get_model_context_limit
            stats["model_context_limit"] = int(get_model_context_limit(ctx.active_model) or 0)
        except Exception:
            stats["model_context_limit"] = 0
        return {"id": rid, "result": stats}

    return None
