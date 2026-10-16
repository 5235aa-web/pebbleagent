#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pricing.save end-to-end (isolated PEBBLE_HOME + real gateway.server module).

Covers: valid save / validation rejects / atomic write / unknown-field preservation /
null-block stripping / hot reload / config.get read path.

Run: python tests/test_pricing_save.py   (exit 0 = pass)
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}  {detail}")

    tmp = tempfile.mkdtemp(prefix="pebble_pricing_test_")
    os.environ["PEBBLE_HOME"] = tmp  # must be set before importing gateway.server

    ok_models = {
        "test-model": {
            "display_name": "Test Model", "provider": "testco", "currency": "CNY",
            "pricing": {"input_per_million": 1.5, "cache_hit_per_million": 0.01, "output_per_million": 3},
            "custom_unknown_field": {"foo": "bar"},
        },
        "peak-model": {
            "display_name": "Peak Model", "provider": "testco",
            "pricing": {"input_per_million": 2, "cache_hit_per_million": 0.04, "output_per_million": 8},
            "pricing_peak": {"input_per_million": 2, "cache_hit_per_million": 0.04, "output_per_million": 8},
            "pricing_offpeak": {"input_per_million": 1, "cache_hit_per_million": 0.02, "output_per_million": 4},
        },
    }

    print("=" * 62)
    print("1) import gateway.server (isolated PEBBLE_HOME)")
    import gateway.server as sv
    print(f"   PEBBLE_HOME = {tmp}")

    print("2) pre-hot-reload: initial load (no data-dir file -> project-root version)")
    svc = sv.AgentChatServiceV2.__new__(sv.AgentChatServiceV2)
    svc._model_pricing_mtime = 0.0
    svc._model_pricing = {}
    svc._model_pricing = svc._load_model_pricing()
    check("initial load ok (project root version)", bool(svc._model_pricing.get("models")),
          f"mtime={svc._model_pricing_mtime}")
    root_mtime = svc._model_pricing_mtime
    check("initial mtime recorded (>0)", root_mtime > 0)

    print("3) save (valid, full payload)")
    r = sv.handle_request("pricing.save", {"models": ok_models, "default_currency": "CNY"}, "t1")
    check("returns ok", isinstance(r, dict) and r.get("result", {}).get("ok") is True, f"resp={r}")
    check("count=2", r.get("result", {}).get("count") == 2)

    path = os.path.join(tmp, "model_pricing.json")
    check("data-dir file created", os.path.exists(path))
    check("no .tmp leftovers", not os.path.exists(path + ".tmp"))
    data = json.load(open(path, "r", encoding="utf-8"))
    check("default currency written", data.get("default_currency") == "CNY")
    m = data["models"]["test-model"]
    check("numeric value written", m["pricing"]["input_per_million"] == 1.5)
    check("unknown field preserved", m.get("custom_unknown_field") == {"foo": "bar"})
    pm = data["models"]["peak-model"]
    check("peak/offpeak preserved", "pricing_peak" in pm and "pricing_offpeak" in pm)

    print("4) hot reload: _ensure_model_pricing_fresh should pick up the new data-dir file")
    time.sleep(0.05)
    svc._ensure_model_pricing_fresh()
    check("reload includes test-model", "test-model" in svc._model_pricing.get("models", {}))
    check("reload updated mtime", svc._model_pricing_mtime != root_mtime)

    print("5) second save (simulating a user price edit)")
    ok_models["test-model"]["pricing"]["input_per_million"] = 9.9
    r = sv.handle_request("pricing.save", {"models": ok_models, "default_currency": "CNY"}, "t2")
    check("second save ok", r.get("result", {}).get("ok") is True)
    time.sleep(0.05)
    svc._ensure_model_pricing_fresh()
    check("second reload price updated",
          svc._model_pricing["models"]["test-model"]["pricing"]["input_per_million"] == 9.9)

    print("6) validation rejects (file must not be corrupted)")
    bad_cases = [
        ("empty models", {"models": {}}),
        ("models not an object", {"models": []}),
        ("missing field", {"models": {"m": {"pricing": {"input_per_million": 1, "cache_hit_per_million": 2}}}}),
        ("negative", {"models": {"m": {"pricing": {"input_per_million": -1, "cache_hit_per_million": 2, "output_per_million": 3}}}}),
        ("non-numeric", {"models": {"m": {"pricing": {"input_per_million": "abc", "cache_hit_per_million": 2, "output_per_million": 3}}}}),
        ("broken peak block", {"models": {"m": {"pricing": {"input_per_million": 1, "cache_hit_per_million": 2, "output_per_million": 3}, "pricing_peak": {"input_per_million": 1}}}}),
        ("entry not an object", {"models": {"m": "not-an-object"}}),
        ("NaN", {"models": {"m": {"pricing": {"input_per_million": float('nan'), "cache_hit_per_million": 2, "output_per_million": 3}}}}),
    ]
    for label, params in bad_cases:
        r = sv.handle_request("pricing.save", params, "t3")
        check(f"reject: {label}", r.get("result", {}).get("ok") is False, f"resp={r}")

    data2 = json.load(open(path, "r", encoding="utf-8"))
    check("file intact after rejects", data2["models"]["test-model"]["pricing"]["input_per_million"] == 9.9)

    print("7) string-number sanitization + null-block stripping")
    r = sv.handle_request("pricing.save", {"models": {"str-model": {"pricing": {"input_per_million": "1.25", "cache_hit_per_million": "0.5", "output_per_million": "2"}, "pricing_peak": None}}}, "t4")
    check("string-number save ok", r.get("result", {}).get("ok") is True)
    data3 = json.load(open(path, "r", encoding="utf-8"))
    sm = data3["models"]["str-model"]
    check("string->float", sm["pricing"]["input_per_million"] == 1.25 and isinstance(sm["pricing"]["input_per_million"], float))
    check("null block stripped", "pricing_peak" not in sm)

    print("8) config.get pricing read path (should read data-dir version)")
    r = sv.handle_request("config.get", {"key": "pricing"}, "t5")
    pd = r.get("result", {}).get("pricing", {})
    check("config.get returns data-dir version", "str-model" in pd.get("models", {}))

    print("=" * 62)
    print(f"RESULT: {passed} passed / {failed} failed")
    if failed == 0:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"temp dir kept: {tmp}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
