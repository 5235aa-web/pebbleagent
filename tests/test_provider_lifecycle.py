#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Provider/model lifecycle: active-provider coherence, deletion repair, atomic
writes and validation.

Run: python tests/test_provider_lifecycle.py   (exit 0 = pass)
"""
import json
import os
import pathlib
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_provider_lifecycle_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import gateway.server as srv  # noqa: E402
from gateway.config import load_llm_config  # noqa: E402
from agent_core.agent_chat_service_v2 import AgentChatServiceV2  # noqa: E402

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


def write(name, obj):
    path = pathlib.Path(TMP) / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def providers(items, active=0):
    return write("llm_providers.json", {"providers": items, "activeProviderIndex": active})


def agent(model="", extra=None):
    entry = {"model_source": model, "custom_model": None, "temperature": None, "max_tokens": 321}
    if extra:
        entry.update(extra)
    return write("agent_model_config.json", {"version": "1.0", "agents": {"code_agent": entry}})


def read_providers():
    with open(pathlib.Path(TMP) / "llm_providers.json", encoding="utf-8") as f:
        return json.load(f)


def read_agent():
    with open(pathlib.Path(TMP) / "agent_model_config.json", encoding="utf-8") as f:
        return json.load(f)["agents"]["code_agent"]


def call(method, params=None, rid="t"):
    return srv.handle_request(method, params or {}, rid) or {}


def main() -> int:
    # ---- a ready appended card becomes active and owns the configured model ----
    providers([{"id": "a", "provider": "alpha", "apiKey": "ka",
                "baseUrl": "https://alpha.example/v1", "models": ["model-a"]}], 0)
    agent("model-a")
    res = call("model.provider.save", {"index": -1, "provider": {
        "name": "beta", "apiKey": "kb", "baseUrl": "https://beta.example/v1",
        "models": ["model-b"],
    }})["result"]
    data = read_providers()
    check("ready append reports the new index", res.get("index") == 1, f"{res}")
    check("ready append keeps the current agent model",
          data.get("activeProviderIndex") == 0 and read_agent().get("model_source") == "model-a",
          f"{data.get('activeProviderIndex')} / {read_agent()}")
    switched = call("model.provider.set", {"index": 1, "model": "model-b"})["result"]
    check("explicit provider switch activates the new card", switched.get("ok") is True, f"{switched}")
    check("explicit provider switch moves the model",
          read_providers().get("activeProviderIndex") == 1
          and read_agent().get("model_source") == "model-b",
          f"{read_providers().get('activeProviderIndex')} / {read_agent()}")

    # ---- a blank appended card is a draft and leaves the active endpoint alone ----
    providers([{"id": "a", "provider": "alpha", "apiKey": "ka",
                "baseUrl": "https://alpha.example/v1", "models": ["model-a"]}], 0)
    agent("model-a")
    res = call("model.provider.save", {"index": -1, "provider": {
        "name": "draft", "apiKey": "", "baseUrl": "", "models": [],
    }})["result"]
    data = read_providers()
    check("blank append reports the new index", res.get("index") == 1, f"{res}")
    check("blank append does not steal active", data.get("activeProviderIndex") == 0,
          f"{data.get('activeProviderIndex')}")
    check("blank append leaves the old model", read_agent().get("model_source") == "model-a",
          f"{read_agent()}")
    options = call("model.options")["result"]
    check("options reports the effective active provider", options.get("provider") == "alpha",
          f"{options.get('provider')}")

    # ---- switching to a draft is rejected ----
    bad = call("model.provider.set", {"index": 1, "model": ""})["result"]
    check("draft provider switch rejected", bad.get("ok") is False, f"{bad}")

    # ---- duplicate model ids: ready active provider wins ----
    providers([
        {"id": "a", "provider": "alpha", "apiKey": "ka",
         "baseUrl": "https://alpha.example/v1", "models": ["shared", "model-a"]},
        {"id": "b", "provider": "beta", "apiKey": "kb",
         "baseUrl": "https://beta.example/v1", "models": ["shared", "model-b"]},
    ], 1)
    agent("shared")
    options = call("model.options")["result"]
    aux = load_llm_config("code_agent")
    main_cfg = AgentChatServiceV2()._get_llm_config("code_agent", "shared")
    check("active card is reported for a duplicate model", options.get("provider") == "beta",
          f"{options.get('provider')}")
    check("aux client uses the active duplicate owner", aux.get("base_url") == "https://beta.example/v1",
          f"{aux.get('base_url')}")
    check("main chat uses the active duplicate owner", main_cfg.get("base_url") == "https://beta.example/v1",
          f"{main_cfg.get('base_url')}")

    # ---- a different agent keeps its own model owner ----
    providers([
        {"id": "a", "provider": "alpha", "apiKey": "ka",
         "baseUrl": "https://alpha.example/v1", "models": ["model-a"]},
        {"id": "b", "provider": "beta", "apiKey": "kb",
         "baseUrl": "https://beta.example/v1", "models": ["model-b"]},
    ], 0)
    write("agent_model_config.json", {"version": "1.0", "agents": {
        "code_agent": {"model_source": "model-a", "custom_model": None},
        "chat_agent": {"model_source": "model-b", "custom_model": None},
    }})
    old_agent = srv._current_agent
    srv._current_agent = "chat_agent"
    chat_cfg = load_llm_config("chat_agent")
    check("per-agent model keeps its own provider",
          chat_cfg.get("base_url") == "https://beta.example/v1"
          and chat_cfg.get("model") == "model-b",
          f"{chat_cfg.get('base_url')} / {chat_cfg.get('model')}")
    switched = call("agent.switch", {"name": "chat_agent"})["result"]
    check("agent switch aligns the active provider card",
          switched.get("ok") is True and read_providers().get("activeProviderIndex") == 1,
          f"{switched} / {read_providers().get('activeProviderIndex')}")
    srv._current_agent = old_agent

    # ---- deleting the active provider repairs the agent model and keeps extras ----
    providers([
        {"id": "a", "provider": "alpha", "apiKey": "ka",
         "baseUrl": "https://alpha.example/v1", "models": ["only-a"]},
        {"id": "b", "provider": "beta", "apiKey": "kb",
         "baseUrl": "https://beta.example/v1", "models": ["only-b"]},
    ], 0)
    agent("only-a")
    res = call("model.provider.delete", {"index": 0})["result"]
    check("delete active reports ok", res.get("ok") is True, f"{res}")
    check("delete active moves to the next ready provider",
          read_providers().get("activeProviderIndex") == 0,
          f"{read_providers().get('activeProviderIndex')}")
    entry = read_agent()
    check("delete active rewrites the model", entry.get("model_source") == "only-b", f"{entry}")
    check("delete active keeps unknown agent fields", entry.get("max_tokens") == 321, f"{entry}")
    aux = load_llm_config("code_agent")
    check("delete active leaves the loader on a real endpoint",
          aux.get("base_url") == "https://beta.example/v1" and aux.get("model") == "only-b",
          f"{aux.get('base_url')} / {aux.get('model')}")

    # ---- removing the last model from the active card repairs the agent ----
    providers([
        {"id": "a", "provider": "alpha", "apiKey": "ka",
         "baseUrl": "https://alpha.example/v1", "models": ["only-a"]},
        {"id": "b", "provider": "beta", "apiKey": "kb",
         "baseUrl": "https://beta.example/v1", "models": ["only-b"]},
    ], 0)
    agent("only-a")
    call("model.provider.save", {"index": 0, "provider": {
        "slug": "a", "name": "alpha", "apiKey": "ka",
        "baseUrl": "https://alpha.example/v1", "models": [],
    }})
    data = read_providers()
    check("removing the last model moves off the empty card",
          data.get("activeProviderIndex") == 1, f"{data.get('activeProviderIndex')}")
    check("removing the last model picks a real remaining model",
          read_agent().get("model_source") == "only-b", f"{read_agent()}")
    check("removing the last model keeps agent extras",
          read_agent().get("max_tokens") == 321, f"{read_agent()}")

    # ---- no providers at all clears the displayed model ----
    providers([], 0)
    agent("stale-model")
    empty = call("model.options")["result"]
    check("no providers clears the model", empty.get("model") == "", f"{empty.get('model')!r}")
    check("no providers returns an empty list", empty.get("providers") == [], f"{empty.get('providers')}")

    # ---- invalid mutations are errors, not silent ok:true ----
    check("unknown model switch is rejected",
          call("model.switch", {"model": "nope"})["result"].get("ok") is False)
    check("bad provider set index is rejected",
          call("model.provider.set", {"index": 9, "model": ""})["result"].get("ok") is False)
    check("bad provider delete index is rejected",
          call("model.provider.delete", {"index": 9})["result"].get("ok") is False)

    # ---- provider save preserves unknown fields, strips hidden shaping ----
    providers([{"id": "a", "provider": "openrouter", "apiKey": "k",
                "baseUrl": "https://openrouter.ai/api/v1", "models": ["m"],
                "customField": {"keep": True},
                "extraHeaders": {"X": "1"}, "sessionHeader": "x-s",
                "reasoningMode": "effort_only", "maxEffort": "keep"}], 0)
    agent("m")
    call("model.provider.save", {"index": 0, "provider": {
        "slug": "a", "name": "openrouter", "apiKey": "k",
        "baseUrl": "https://openrouter.ai/api/v1", "models": ["m"],
        "extraHeaders": {"X": "1"}, "sessionHeader": "x-s",
        "reasoningMode": "effort_only", "maxEffort": "keep",
    }})
    saved = read_providers()["providers"][0]
    check("provider save keeps unknown fields", saved.get("customField") == {"keep": True}, f"{saved}")
    check("non-opencode shaping is removed",
          not any(k in saved for k in ("extraHeaders", "sessionHeader", "reasoningMode", "maxEffort")),
          f"{saved}")

    # ---- validation ----
    providers([{"id": "a", "provider": "alpha", "apiKey": "k",
                "baseUrl": "https://alpha.example/v1", "models": ["m"]}], 0)
    agent("m")
    bad_url = call("model.provider.save", {"index": 0, "provider": {
        "name": "alpha", "apiKey": "k", "baseUrl": "not-a-url", "models": ["m"]}})["result"]
    check("invalid base url is rejected", bad_url.get("ok") is False, f"{bad_url}")
    bad_model = call("model.provider.save", {"index": 0, "provider": {
        "name": "alpha", "apiKey": "k", "baseUrl": "https://alpha.example/v1",
        "models": ["display name"]}})["result"]
    check("model id with spaces is rejected", bad_model.get("ok") is False, f"{bad_model}")
    bad_headers = call("model.provider.save", {"index": 0, "provider": {
        "name": "opencode", "apiKey": "k", "baseUrl": "https://opencode.ai/inference/go/openai/v1",
        "models": ["m"], "extraHeaders": "not-json"}})["result"]
    check("non-object extra headers is rejected", bad_headers.get("ok") is False, f"{bad_headers}")

    # ---- model.switch keeps per-agent extras ----
    providers([{"id": "a", "provider": "alpha", "apiKey": "k",
                "baseUrl": "https://alpha.example/v1", "models": ["m1", "m2"]}], 0)
    agent("m1")
    call("model.switch", {"model": "m2"})
    entry = read_agent()
    check("model.switch writes the model", entry.get("model_source") == "m2", f"{entry}")
    check("model.switch keeps max_tokens", entry.get("max_tokens") == 321, f"{entry}")

    # ---- model.params.set accepts a non-active index ----
    providers([
        {"id": "a", "provider": "alpha", "apiKey": "ka",
         "baseUrl": "https://alpha.example/v1", "models": ["m-a"]},
        {"id": "b", "provider": "beta", "apiKey": "kb",
         "baseUrl": "https://beta.example/v1", "models": ["m-b"]},
    ], 0)
    agent("m-a")
    call("model.params.set", {"index": 1, "temperature": 0.7, "topP": 0.8,
                              "reasoning_effort": "low", "thinking_enabled": False})
    saved_b = read_providers()["providers"][1]
    check("params.set edits the requested card", saved_b.get("temperature") == 0.7, f"{saved_b}")
    check("params.set does not touch the active card",
          read_providers()["providers"][0].get("temperature") is None,
          f"{read_providers()['providers'][0]}")

    # ---- atomic writes leave no temp files and keep one backup ----
    leftovers = list(pathlib.Path(TMP).glob("*.tmp"))
    check("atomic write leaves no temp files", not leftovers, f"{leftovers}")
    check("atomic write keeps the previous file",
          (pathlib.Path(TMP) / "llm_providers.json.bak").exists(),
          "no .bak")

    # ---- corrupt user file does not fall back to bundled sample providers ----
    with open(pathlib.Path(TMP) / "llm_providers.json", "w", encoding="utf-8") as f:
        f.write('{"providers": [')
    data = srv._read_providers()
    check("corrupt user file returns empty, not bundled samples",
          data.get("providers") == [], f"{data}")

    # ---- agent selection persists and restores ----
    write("llm_providers.json", {"providers": [
        {"id": "a", "provider": "alpha", "apiKey": "k",
         "baseUrl": "https://alpha.example/v1", "models": ["m"]}], "activeProviderIndex": 0})
    agent("m")
    switched = call("agent.switch", {"name": "chat_agent"})["result"]
    check("agent switch accepts a real agent", switched.get("ok") is True, f"{switched}")
    with open(pathlib.Path(TMP) / "agent_model_config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    check("agent selection is persisted", cfg.get("current_agent") == "chat_agent", f"{cfg.get('current_agent')}")
    srv._current_agent = "code_agent"
    srv.restore_current_agent()
    check("agent selection is restored", srv._current_agent == "chat_agent", srv._current_agent)

    print()
    if failed:
        print(f"FAILED: {failed} / {passed + failed}")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
