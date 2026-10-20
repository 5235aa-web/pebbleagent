#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Thinking toggle & effort: build_thinking_params + _bool + server.py expression semantics.

Functions are extracted from real source via AST (isolated, no full module import).
Run: python tests/test_thinking_params.py   (exit 0 = pass)
"""
import ast
import os
import sys
from typing import Any, Dict, Optional

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

    def extract_funcs(path, names):
        """Extract top-level function sources via AST (isolated test, no full module import)."""
        with open(path, "r", encoding="utf-8-sig") as f:
            src = f.read()
        tree = ast.parse(src)
        out = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                out[node.name] = ast.get_source_segment(src, node)
        return out

    print("=" * 62)
    print("1) build_thinking_params (extracted from agent_core/agent_chat_service_v2.py)")
    svc_path = os.path.join(ROOT, "agent_core", "agent_chat_service_v2.py")
    srcs = extract_funcs(svc_path, {"build_thinking_params"})
    assert "build_thinking_params" in srcs, "build_thinking_params not found"
    ns = {"Dict": Dict, "Any": Any}
    exec(srcs["build_thinking_params"], ns)
    btp = ns["build_thinking_params"]

    r = btp("deepseek-v4-flash", True, "max")
    check("deepseek on/max keeps max", r["reasoning_effort"] == "max" and r["extra_body"] == {"thinking": {"type": "enabled"}}, str(r))

    r = btp("mimo-v2.5-pro", True, "max")
    check("mimo on/max downgrades to high", r["reasoning_effort"] == "high" and r["extra_body"]["thinking"]["type"] == "enabled", str(r))

    for v in ("low", "medium", "high"):
        r = btp("mimo-v2-pro", True, v)
        check(f"mimo on/{v} kept as-is", r["reasoning_effort"] == v, str(r))

    r = btp("gpt-4o", True, "max")
    check("gpt-4o on/max downgrades to high", r["reasoning_effort"] == "high", str(r))

    r = btp("deepseek-v4-flash", False, "max")
    check("deepseek off -> thinking:disabled, no effort field", r.get("extra_body") == {"thinking": {"type": "disabled"}} and "reasoning_effort" not in r, str(r))

    r = btp("mimo-v2.5-pro", False, "high")
    check("mimo off -> thinking:disabled, no effort field", r.get("extra_body") == {"thinking": {"type": "disabled"}} and "reasoning_effort" not in r, str(r))

    r = btp("", True, "max")
    check("empty model name on/max downgrades to high", r["reasoning_effort"] == "high", str(r))

    r = btp("DeepSeek-V4-Pro", True, "max")
    check("uppercase DeepSeek keeps max (case-insensitive)", r["reasoning_effort"] == "max", str(r))

    r = btp("glm-5.3", True, "max", True)
    check("keep_max keeps max for non-DeepSeek", r["reasoning_effort"] == "max", str(r))

    r = btp("glm-5.3", True, "max")
    check("keep_max defaults off (glm still downgraded)", r["reasoning_effort"] == "high", str(r))

    r = btp("glm-5.3", True, "max", False)
    check("explicit keep_max=False downgrades", r["reasoning_effort"] == "high", str(r))

    print("=" * 62)
    print("2) _bool (extracted from gateway/server.py)")
    server_path = os.path.join(ROOT, "gateway", "server.py")
    srcs2 = extract_funcs(server_path, {"_bool"})
    assert "_bool" in srcs2, "_bool not found"
    ns2 = {"Optional": Optional}
    exec(srcs2["_bool"], ns2)
    _bool = ns2["_bool"]

    check("_bool(None) -> None", _bool(None) is None)
    check("_bool(True) -> True", _bool(True) is True)
    check("_bool(False) -> False", _bool(False) is False)
    check("_bool('true') -> True", _bool("true") is True)
    check("_bool('False') -> False", _bool("False") is False)
    check("_bool('1') -> True", _bool("1") is True)
    check("_bool('0') -> False", _bool("0") is False)
    check("_bool('yes') -> True", _bool("yes") is True)
    check("_bool('no') -> False", _bool("no") is False)
    check("_bool('on') -> True", _bool("on") is True)
    check("_bool('off') -> False", _bool("off") is False)
    check("_bool('garbage') -> None", _bool("garbage") is None)
    check("_bool('garbage', True) -> True (default fallback)", _bool("garbage", True) is True)
    check("_bool(None, True) -> True (default fallback)", _bool(None, True) is True)
    check("_bool('', False) -> False (empty string goes to default)", _bool("", False) is False)
    check("_bool(False, True) -> False (explicit False wins)", _bool(False, True) is False)

    print("=" * 62)
    print("3) server.py expression semantics + source presence checks")

    def opt_semantics(p):
        return p.get("thinking_enabled") is not False

    check("options/read: missing -> True (legacy config unchanged)", opt_semantics({}) is True)
    check("options/read: False -> False", opt_semantics({"thinking_enabled": False}) is False)
    check("options/read: True -> True", opt_semantics({"thinking_enabled": True}) is True)
    check("options/read: None -> True", opt_semantics({"thinking_enabled": None}) is True)

    check("save: missing -> True", _bool({}.get("thinking_enabled"), True) is True)
    check("save: False -> False", _bool({"thinking_enabled": False}.get("thinking_enabled"), True) is False)
    check("save: 'false' -> False", _bool({"thinking_enabled": "false"}.get("thinking_enabled"), True) is False)
    check("save: garbage -> True (conservative default on)", _bool({"thinking_enabled": "xx"}.get("thinking_enabled"), True) is True)

    def read_gateway_source():
        """server.py plus its RPC domain modules (handlers moved out of the entry file)."""
        parts = []
        rpc_dir = os.path.join(ROOT, "gateway", "rpc")
        rels = ["gateway/server.py"] + [
            os.path.join("gateway", "rpc", n)
            for n in sorted(os.listdir(rpc_dir)) if n.endswith(".py")
        ]
        for rel in rels:
            with open(os.path.join(ROOT, rel), "r", encoding="utf-8-sig") as f:
                parts.append(f.read())
        return "\n".join(parts)

    server_src = read_gateway_source()
    check("source: model.options output field", '"thinking_enabled": p.get("thinking_enabled") is not False' in server_src)
    check("source: prompt_submit read field", 'thinking_enabled = prov.get("thinking_enabled") is not False' in server_src)
    check("source: save persists field", '_bool(provider_data.get("thinking_enabled"), True)' in server_src)
    check("source: params.set updates field", '_bool(params.get("thinking_enabled"))' in server_src)
    check("source: call site passes thinking_enabled", 'thinking_enabled=thinking_enabled,' in server_src)
    check("source: old derivation removed", 'thinking_enabled=reasoning_effort is not None' not in server_src)

    with open(svc_path, "r", encoding="utf-8-sig") as f:
        svc_src = f.read()
    check("source: chat_stream unified build call", 'stream_params.update(build_thinking_params(use_model, final_thinking_enabled, final_reasoning_effort, keep_max_effort(state.llm_config)))' in svc_src)
    check("source: title-gen unified build call", 'title_params.update(build_thinking_params(use_model, thinking_enabled, reasoning_effort, keep_max_effort(llm_config)))' in svc_src)
    check("source: title-gen call site args", 'generate_conversation_title(current_messages, use_model, final_thinking_enabled, final_reasoning_effort, conversation_uuid)' in svc_src)

    print("=" * 62)
    print(f"RESULT: {passed} passed / {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
