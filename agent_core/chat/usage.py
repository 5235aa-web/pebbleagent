"""Token usage accounting and pricing."""

import json
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class UsageTracker:
    """Pricing table, per-call cost and per-session accumulation."""

    def __init__(self, data_home: str, project_root: str,
                 period_fn: Optional[Callable[[], str]] = None):
        self.data_home = data_home
        self.project_root = project_root
        self.period_fn = period_fn or (lambda: "offpeak")
        self.pricing: Dict[str, Any] = {"models": {}}
        self.pricing_mtime = 0.0
        self.sessions: Dict[str, dict] = {}
        self.reload()

    def _config_paths(self):
        return (os.path.join(self.data_home, "model_pricing.json"),
                os.path.join(self.project_root, "model_pricing.json"))

    def _read_config(self) -> Optional[dict]:
        for path in self._config_paths():
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        return json.load(fh)
                except Exception:
                    continue
        return None

    def reload(self) -> dict:
        data = self._read_config()
        if data:
            for path in self._config_paths():
                if os.path.exists(path):
                    self.pricing_mtime = os.path.getmtime(path)
                    break
        self.pricing = data or {"models": {}}
        return self.pricing

    def ensure_fresh(self) -> None:
        if self.pricing_mtime <= 0 and self.pricing.get("models"):
            return
        try:
            for path in self._config_paths():
                if os.path.exists(path):
                    if os.path.getmtime(path) != self.pricing_mtime:
                        self.reload()
                    return
        except Exception:
            pass

    def calculate_cost(self, model: str, usage) -> Optional[dict]:
        """Cost for one API call; None when the model has no price entry."""
        self.ensure_fresh()

        pricing_data = self.pricing.get("models", {}).get(model)
        if not pricing_data:
            return None

        pricing = pricing_data.get("pricing", {})
        period = None

        prompt_tokens = getattr(usage, "prompt_tokens", 0)
        tier = pricing_data.get("pricing_tier_high")
        if tier and prompt_tokens > tier.get("threshold_tokens", 256000):
            pricing = tier
        else:
            peak = pricing_data.get("pricing_peak")
            offpeak = pricing_data.get("pricing_offpeak")
            if peak and offpeak:
                period = self.period_fn()
                pricing = peak if period == "peak" else offpeak

        cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0)
        cache_miss = prompt_tokens - cache_hit if cache_hit > 0 else prompt_tokens
        completion = getattr(usage, "completion_tokens", 0)

        if cache_hit > 0:
            input_cost = (cache_miss / 1_000_000) * pricing.get("input_per_million", 0)
            cache_cost = (cache_hit / 1_000_000) * pricing.get("cache_hit_per_million", 0)
        else:
            input_cost = (prompt_tokens / 1_000_000) * pricing.get("input_per_million", 0)
            cache_cost = 0.0
            cache_hit = 0
            cache_miss = prompt_tokens

        output_cost = (completion / 1_000_000) * pricing.get("output_per_million", 0)

        return {
            "input_tokens": cache_miss,
            "cache_hit_tokens": cache_hit,
            "output_tokens": completion,
            "input_cost": round(input_cost, 6),
            "cache_hit_cost": round(cache_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": round(input_cost + cache_cost + output_cost, 6),
            "period": period,
            "pricing_used": dict(pricing) if pricing else None,
        }

    def accumulate(self, conversation_uuid: str, cost_info: dict,
                   model_name: str = None) -> dict:
        """Add one call to the session total and persist the call record."""
        if conversation_uuid not in self.sessions:
            existing = self.load_session_cost(conversation_uuid)
            self.sessions[conversation_uuid] = existing or {
                "total_cost": 0.0,
                "total_input_tokens": 0,
                "total_cache_hit_tokens": 0,
                "total_output_tokens": 0,
                "api_calls": 0,
                "currency": "CNY",
            }

        session = self.sessions[conversation_uuid]
        session["total_cost"] += cost_info["total_cost"]
        session["total_input_tokens"] += cost_info["input_tokens"] + cost_info["cache_hit_tokens"]
        session["total_cache_hit_tokens"] += cost_info["cache_hit_tokens"]
        session["total_output_tokens"] += cost_info["output_tokens"]
        session["api_calls"] += 1

        try:
            from .. import agent_chat_history as history
            conv = history.get_agent_conversation_by_uuid(conversation_uuid)
            if conv:
                history.save_api_call_cost(
                    conversation_id=conv["id"],
                    model_name=model_name or "unknown",
                    api_call_index=session["api_calls"],
                    input_tokens=cost_info["input_tokens"],
                    cache_hit_tokens=cost_info["cache_hit_tokens"],
                    output_tokens=cost_info["output_tokens"],
                    input_cost=cost_info["input_cost"],
                    cache_hit_cost=cost_info["cache_hit_cost"],
                    output_cost=cost_info["output_cost"],
                    total_cost=cost_info["total_cost"],
                    currency="CNY",
                )
        except Exception as e:
            logger.warning(f"[Cost] Failed to persist cost: {e}")

        return {**session, "this_call": cost_info}

    def load_session_cost(self, conversation_uuid: str) -> Optional[dict]:
        try:
            from .. import agent_chat_history as history
            conv = history.get_agent_conversation_by_uuid(conversation_uuid)
            if conv:
                summary = history.get_conversation_cost_summary(conv["id"])
                if summary and summary.get("api_calls", 0) > 0:
                    return {
                        "total_cost": summary["total_cost"],
                        "total_input_tokens": summary["total_input_tokens"],
                        "total_cache_hit_tokens": summary["total_cache_hit_tokens"],
                        "total_output_tokens": summary["total_output_tokens"],
                        "api_calls": summary["api_calls"],
                        "currency": summary.get("currency", "CNY"),
                    }
        except Exception as e:
            logger.debug(f"[Cost] Failed to load session cost: {e}")
        return None
