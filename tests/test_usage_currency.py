#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Multi-currency pricing, billing persistence, exchange-rate and mixed-currency aggregation tests.

Run: python tests/test_usage_currency.py   (exit 0 = pass)
"""
import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_usage_currency_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import agent_core.agent_chat_history as history  # noqa: E402
from agent_core.chat.usage import UsageTracker  # noqa: E402
import gateway.server as sv  # noqa: E402

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


class _UsageObj:
    def __init__(self, prompt_tokens, completion_tokens, cached=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_cache_hit_tokens = cached


def main() -> int:
    try:
        # 1. Save a multi-currency pricing table (1 CNY model + 1 USD model) + custom exchange rate 7.0
        models = {
            "cn-model": {
                "currency": "CNY",
                "pricing": {"input_per_million": 2.0, "cache_hit_per_million": 0.5, "output_per_million": 8.0},
            },
            "us-model": {
                "currency": "USD",
                "pricing": {"input_per_million": 3.0, "cache_hit_per_million": 0.3, "output_per_million": 15.0},
            },
        }
        r = sv.handle_request("pricing.save", {"models": models, "default_currency": "USD", "cny_per_usd": 7.0}, "1")
        res = (r or {}).get("result") or {}
        check("pricing.save accepts per-model currency & cny_per_usd", res.get("ok") is True and res.get("cny_per_usd") == 7.0, f"got {res}")

        r_bad = sv.handle_request("pricing.save", {"models": {"m": {"currency": "EUR", "context_limit": 1000}}}, "1b")
        check("pricing.save rejects unsupported currency", ((r_bad or {}).get("result") or {}).get("ok") is False)

        # 2. UsageTracker calculates per-model currency accurately and persists real currency per row
        tracker = UsageTracker(data_home=TMP, project_root=ROOT)
        c_cn = tracker.calculate_cost("cn-model", _UsageObj(1_000_000, 0))
        c_us = tracker.calculate_cost("us-model", _UsageObj(1_000_000, 0))
        check("cn-model billed in CNY", c_cn is not None and c_cn["currency"] == "CNY" and abs(c_cn["total_cost"] - 2.0) < 1e-6, f"got {c_cn}")
        check("us-model billed in USD", c_us is not None and c_us["currency"] == "USD" and abs(c_us["total_cost"] - 3.0) < 1e-6, f"got {c_us}")

        # 3. Accumulate both calls into one conversation and verify mixed aggregation (no blind USD + CNY addition!)
        conv_uuid = str(uuid.uuid4())
        history.create_agent_conversation("u1", "code_agent", title="currency test", uuid=conv_uuid)
        tracker.accumulate(conv_uuid, c_cn, model_name="cn-model")  # 2.0 CNY = 2/7 USD
        s2 = tracker.accumulate(conv_uuid, c_us, model_name="us-model")  # + 3.0 USD
        expected_usd = 3.0 + (2.0 / 7.0)
        check("in-memory session accumulates in default_currency (USD) at 7.0 rate",
              s2.get("currency") == "USD" and abs(s2["total_cost"] - expected_usd) < 1e-4,
              f"got {s2}")

        # 4. usage.stats reads DB rows, keeps per-call currency, and converts mixed buckets properly
        r_stats = sv.handle_request("usage.stats", {"range": "session", "session_id": conv_uuid}, "2")
        st = (r_stats or {}).get("result") or {}
        check("usage.stats returns base currency USD and rate 7.0",
              st.get("currency") == "USD" and st.get("cny_per_usd") == 7.0,
              f"got currency={st.get('currency')} rate={st.get('cny_per_usd')}")
        check("usage.stats converts mixed CNY+USD total_cost accurately",
              abs(float(st.get("total_cost", 0)) - expected_usd) < 1e-4,
              f"expected {expected_usd}, got {st.get('total_cost')}")
        calls = st.get("calls") or []
        check("usage.stats preserves per-call currency on each bill row",
              len(calls) == 2 and calls[0].get("currency") == "CNY" and calls[1].get("currency") == "USD",
              f"got {calls}")

        # 5. pricing.set_rate updates rate and usage.stats reflects the new rate immediately
        r_rate = sv.handle_request("pricing.set_rate", {"cny_per_usd": 8.0}, "3")
        check("pricing.set_rate succeeds", ((r_rate or {}).get("result") or {}).get("cny_per_usd") == 8.0)
        r_stats2 = sv.handle_request("usage.stats", {"range": "session", "session_id": conv_uuid}, "4")
        st2 = (r_stats2 or {}).get("result") or {}
        check("usage.stats recalculates with updated rate 8.0",
              abs(float(st2.get("total_cost", 0)) - (3.0 + 2.0 / 8.0)) < 1e-4,
              f"got {st2.get('total_cost')}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
