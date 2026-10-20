#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Provider-specific headers and reasoning-mode shaping.

Covers: header building (verbatim extras, session header from the conversation
id), reasoning-mode shaping (default keeps the `thinking` block, "effort_only"
drops it), config loading of the new optional provider fields, and static
guards that every client creation site passes the headers through.

Run: python tests/test_provider_headers.py   (exit 0 = pass)
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_provider_headers_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

from agent_core.llm_client import apply_reasoning_mode, build_default_headers  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name} {detail}")


def main() -> int:
    # ---- build_default_headers ----
    check("no config -> None", build_default_headers(None) is None)
    check("empty config -> None", build_default_headers({}) is None)
    check("empty extras -> None", build_default_headers({"extra_headers": {}}) is None)

    h = build_default_headers({"extra_headers": {"User-Agent": "PebbleAgent/1.0"}})
    check("extra headers passed verbatim", h == {"User-Agent": "PebbleAgent/1.0"}, f"{h}")

    h = build_default_headers({"session_header": "x-opencode-session"}, "sess-42")
    check("session header carries the conversation id",
          h == {"x-opencode-session": "sess-42"}, f"{h}")

    check("session header without id -> None",
          build_default_headers({"session_header": "x-opencode-session"}) is None)
    check("empty session header name ignored",
          build_default_headers({"session_header": ""}, "sess-42") is None)

    h = build_default_headers(
        {"extra_headers": {"User-Agent": "PebbleAgent/1.0", "X-Empty": None},
         "session_header": "x-opencode-session"},
        "sess-42",
    )
    check("extras and session header combine",
          h == {"User-Agent": "PebbleAgent/1.0", "x-opencode-session": "sess-42"}, f"{h}")

    # ---- apply_reasoning_mode ----
    params = {"model": "deepseek-v4.1-flash",
              "extra_body": {"thinking": {"type": "enabled"}},
              "reasoning_effort": "high"}
    apply_reasoning_mode(params, {"reasoning_mode": "thinking"})
    check("default mode keeps the thinking block",
          params.get("extra_body") == {"thinking": {"type": "enabled"}}, f"{params}")

    params = {"model": "glm-5.3", "extra_body": {"thinking": {"type": "enabled"}},
              "reasoning_effort": "high"}
    apply_reasoning_mode(params, {"reasoning_mode": "effort_only"})
    check("effort_only drops thinking", "extra_body" not in params, f"{params}")
    check("effort_only keeps reasoning_effort", params.get("reasoning_effort") == "high", f"{params}")

    params = {"model": "m", "extra_body": {"thinking": {"type": "disabled"}, "other": 1}}
    apply_reasoning_mode(params, {"reasoning_mode": "effort_only"})
    check("effort_only keeps unrelated extra_body keys",
          params.get("extra_body") == {"other": 1}, f"{params}")

    params = {"model": "m"}
    apply_reasoning_mode(params, {"reasoning_mode": "effort_only"})
    check("effort_only without extra_body is a no-op", params == {"model": "m"}, f"{params}")

    params = {"model": "m", "extra_body": {"thinking": {"type": "enabled"}}}
    apply_reasoning_mode(params, None)
    check("missing config behaves like default", "extra_body" in params, f"{params}")

    # ---- load_llm_config reads the optional provider fields ----
    import gateway.config as cfg
    cfg._DATA_DIR = __import__("pathlib").Path(TMP)
    providers_path = os.path.join(TMP, "llm_providers.json")

    with open(providers_path, "w", encoding="utf-8") as f:
        json.dump({
            "providers": [{
                "id": "plain", "provider": "deepseek", "apiKey": "k1",
                "baseUrl": "https://api.deepseek.com/v1/", "models": ["deepseek-flash"],
            }],
            "activeProviderIndex": 0,
        }, f)
    plain = cfg.load_llm_config("code_agent")
    check("plain provider: no extra headers", plain["extra_headers"] == {}, f"{plain['extra_headers']}")
    check("plain provider: no session header", plain["session_header"] == "", f"{plain['session_header']}")
    check("plain provider: default reasoning mode", plain["reasoning_mode"] == "thinking", f"{plain['reasoning_mode']}")
    check("plain provider: default max effort", plain["max_effort"] == "downgrade", f"{plain['max_effort']}")

    with open(providers_path, "w", encoding="utf-8") as f:
        json.dump({
            "providers": [{
                "id": "opencode-go", "provider": "opencode", "apiKey": "k2",
                "baseUrl": "https://opencode.ai/inference/go/openai/v1",
                "models": ["glm-5.3-flash"],
                "extraHeaders": {"User-Agent": "PebbleAgent/1.0"},
                "sessionHeader": "x-opencode-session",
                "reasoningMode": "effort_only",
                "maxEffort": "keep",
            }],
            "activeProviderIndex": 0,
        }, f)
    go = cfg.load_llm_config("code_agent")
    check("configured provider: extra headers", go["extra_headers"] == {"User-Agent": "PebbleAgent/1.0"}, f"{go['extra_headers']}")
    check("configured provider: session header", go["session_header"] == "x-opencode-session", f"{go['session_header']}")
    check("configured provider: reasoning mode", go["reasoning_mode"] == "effort_only", f"{go['reasoning_mode']}")
    check("configured provider: max effort", go["max_effort"] == "keep", f"{go['max_effort']}")

    with open(providers_path, "w", encoding="utf-8") as f:
        json.dump({
            "providers": [{
                "id": "bad", "provider": "x", "apiKey": "k3",
                "baseUrl": "https://x/v1", "models": ["m"],
                "reasoningMode": "nonsense",
            }],
            "activeProviderIndex": 0,
        }, f)
    bad = cfg.load_llm_config("code_agent")
    check("invalid reasoning mode falls back to thinking",
          bad["reasoning_mode"] == "thinking", f"{bad['reasoning_mode']}")

    # ---- static guards: every client creation site passes the headers ----
    def src(rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    turn = src(os.path.join("agent_core", "chat", "turn_executor.py"))
    check("turn client passes headers", "default_headers=build_default_headers(llm_config, conversation_uuid)" in turn)

    svc = src(os.path.join("agent_core", "agent_chat_service_v2.py"))
    check("agent title client passes headers",
          "default_headers=build_default_headers(llm_config, conversation_uuid)" in svc)
    check("main stream applies reasoning mode", "apply_reasoning_mode(stream_params, state.llm_config)" in svc)
    check("agent title applies reasoning mode", "apply_reasoning_mode(title_params, llm_config)" in svc)

    title = src(os.path.join("gateway", "session_title.py"))
    check("gateway title client passes headers",
          "default_headers=build_default_headers(config, session_id)" in title)
    check("gateway title applies reasoning mode",
          "apply_reasoning_mode(" in title and "keep_max_effort(config)" in title)

    server = src(os.path.join("gateway", "server.py"))
    check("compress client passes headers",
          "default_headers=build_default_headers(llm_config, session_id)" in server)
    check("title call forwards the session id",
          "generate_session_title(llm_config, user_text, assistant_text, sid)" in server)

    rpc = src(os.path.join("gateway", "rpc", "session.py"))
    check("compress rpc forwards the session id", "srv._get_llm_client(sid)" in rpc)

    # ---- the settings UI save must not drop hand-edited shaping fields ----
    import pathlib
    import gateway.server as srv
    srv._PROVIDERS_PATH = pathlib.Path(providers_path)
    with open(providers_path, "w", encoding="utf-8") as f:
        json.dump({
            "providers": [{
                "id": "opencode-go", "provider": "opencode", "apiKey": "k",
                "baseUrl": "https://opencode.ai/inference/go/openai/v1",
                "models": ["glm-5.3-flash"],
                "extraHeaders": {"User-Agent": "PebbleAgent/1.0"},
                "sessionHeader": "x-opencode-session",
                "reasoningMode": "effort_only",
                "maxEffort": "keep",
            }],
            "activeProviderIndex": 0,
        }, f)
    srv.handle_request("model.provider.save", {
        "index": 0,
        "provider": {
            "slug": "opencode-go", "name": "opencode", "apiKey": "k",
            "baseUrl": "https://opencode.ai/inference/go/openai/v1",
            "models": ["glm-5.3-flash"], "temperature": 0.2,
        },
    }, "1")
    with open(providers_path, encoding="utf-8") as f:
        saved = json.load(f)["providers"][0]
    check("settings save keeps extraHeaders",
          saved.get("extraHeaders") == {"User-Agent": "PebbleAgent/1.0"}, f"{saved.get('extraHeaders')}")
    check("settings save keeps sessionHeader",
          saved.get("sessionHeader") == "x-opencode-session", f"{saved.get('sessionHeader')}")
    check("settings save keeps reasoningMode",
          saved.get("reasoningMode") == "effort_only", f"{saved.get('reasoningMode')}")
    check("settings save keeps maxEffort",
          saved.get("maxEffort") == "keep", f"{saved.get('maxEffort')}")
    check("settings save still applies edited fields",
          saved.get("temperature") == 0.2, f"{saved.get('temperature')}")

    # ---- the settings panel reads the shaping fields back ----
    opts = srv.handle_request("model.options", {}, "2") or {}
    listed = ((opts.get("result") or {}).get("providers") or [{}])[0]
    check("model.options exposes extraHeaders",
          listed.get("extraHeaders") == {"User-Agent": "PebbleAgent/1.0"}, f"{listed.get('extraHeaders')}")
    check("model.options exposes sessionHeader",
          listed.get("sessionHeader") == "x-opencode-session", f"{listed.get('sessionHeader')}")
    check("model.options exposes reasoningMode",
          listed.get("reasoningMode") == "effort_only", f"{listed.get('reasoningMode')}")
    check("model.options exposes maxEffort",
          listed.get("maxEffort") == "keep", f"{listed.get('maxEffort')}")

    # ---- clearing a field in the UI removes it from the file ----
    srv.handle_request("model.provider.save", {"index": 0, "provider": {
        "slug": "opencode-go", "name": "opencode", "apiKey": "k",
        "baseUrl": "https://opencode.ai/inference/go/openai/v1",
        "models": ["glm-5.3-flash"], "temperature": 0.2, "topP": 0.2, "maxTokens": 4096,
        "extraHeaders": {}, "sessionHeader": "", "reasoningMode": "thinking",
    }}, "3")
    with open(providers_path, encoding="utf-8") as f:
        cleared = json.load(f)["providers"][0]
    check("clearing extraHeaders removes the field", "extraHeaders" not in cleared, f"{cleared}")
    check("clearing sessionHeader removes the field", "sessionHeader" not in cleared, f"{cleared}")
    check("reasoningMode back to default", cleared.get("reasoningMode") == "thinking",
          f"{cleared.get('reasoningMode')}")

    # ---- the agent-side config (main chat path) must carry the shaping fields ----
    with open(providers_path, "w", encoding="utf-8") as f:
        json.dump({"providers": [{
            "id": "opencode-go", "provider": "opencode", "apiKey": "k",
            "baseUrl": "https://opencode.ai/inference/go/openai/v1",
            "models": ["glm-5.3-flash"],
            "extraHeaders": {"User-Agent": "PebbleAgent/1.0"},
            "sessionHeader": "x-opencode-session",
            "reasoningMode": "effort_only",
            "maxEffort": "keep",
        }], "activeProviderIndex": 0}, f)
    from agent_core.agent_chat_service_v2 import AgentChatServiceV2
    svc_cfg = AgentChatServiceV2()._get_llm_config("code_agent", "glm-5.3-flash")
    check("agent config carries session header",
          svc_cfg.get("session_header") == "x-opencode-session", f"{svc_cfg.get('session_header')}")
    check("agent config carries extra headers",
          svc_cfg.get("extra_headers") == {"User-Agent": "PebbleAgent/1.0"}, f"{svc_cfg.get('extra_headers')}")
    check("agent config carries reasoning mode",
          svc_cfg.get("reasoning_mode") == "effort_only", f"{svc_cfg.get('reasoning_mode')}")
    check("agent config carries max effort",
          svc_cfg.get("max_effort") == "keep", f"{svc_cfg.get('max_effort')}")

    print()
    if failed:
        print(f"FAILED: {failed} / {passed + failed}")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
