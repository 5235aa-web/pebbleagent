#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Session auto-title generation.

Covers: language detection, title normalization (labels/quotes/punctuation/
length caps), the two-attempt model call (thinking disabled, then plain), and
static guards that the old "first 50 chars" naming is gone and the generated
title is persisted with a compare-and-set.

Run: python tests/test_session_title.py   (exit 0 = pass)
"""
import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_session_title_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP


def _reply(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
    )


def main() -> int:
    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name} {detail}")

    from gateway.session_title import (
        MAX_TITLE_CHARS,
        MAX_TITLE_WORDS,
        clean_title,
        detect_title_language,
        generate_session_title,
    )

    # ---- language detection ----
    check("zh text -> zh", detect_title_language("帮我看下这个报错") == "zh")
    check("latin text -> en", detect_title_language("why does the build fail") == "en")
    check("empty -> en", detect_title_language("") == "en")
    check("mixed with cjk -> zh", detect_title_language("fix 这个 bug") == "zh")

    # ---- clean_title ----
    check("label stripped", clean_title("标题：修复登录问题") == "修复登录问题")
    check("english label stripped", clean_title("Title: Add dark mode") == "Add dark mode")
    check("wrapping quotes stripped", clean_title('"Fix login bug."') == "Fix login bug")
    check("cjk quotes stripped", clean_title("《项目架构梳理》") == "项目架构梳理")
    check("trailing punctuation stripped", clean_title("Add dark mode!") == "Add dark mode")
    check("first non-empty line wins", clean_title("\n\n  Fix build  \nsecond") == "Fix build")
    check("empty -> None", clean_title("   \n  ") is None)
    check("punctuation only -> None", clean_title("。。。") is None)

    long_zh = "这是一个非常长的中文标题用来验证截断逻辑是否生效并且不超过限制"
    zh_title = clean_title(long_zh)
    check(
        "zh truncated to cap",
        zh_title is not None and len(zh_title) <= MAX_TITLE_CHARS,
        repr(zh_title),
    )

    long_en = (
        "one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen"
    )
    en_title = clean_title(long_en)
    check(
        "en truncated to cap",
        en_title is not None and len(en_title.split()) <= MAX_TITLE_WORDS,
        repr(en_title),
    )

    # ---- model call ----
    class _FakeCompletions:
        def __init__(self):
            self.calls = []
            self.responses = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    class _FakeClient:
        def __init__(self, completions):
            self.chat = types.SimpleNamespace(completions=completions)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    fake = _FakeCompletions()
    import openai

    original_client = openai.AsyncOpenAI
    openai.AsyncOpenAI = lambda **kwargs: _FakeClient(fake)
    try:
        fake.responses = [_reply("修复登录问题")]
        title = asyncio.run(
            generate_session_title(
                {"model": "test-model", "api_key": "k", "base_url": "http://127.0.0.1:1"},
                "登录坏了",
                "我看下",
            )
        )
        check("generates title", title == "修复登录问题", repr(title))
        check("call is non-streaming", bool(fake.calls) and fake.calls[0].get("stream") is False)
        check("first call disables thinking", "extra_body" in fake.calls[0])
        check("call caps output tokens", fake.calls[0].get("max_tokens", 0) <= 128)

        fake.calls.clear()
        fake.responses = [RuntimeError("400 thinking not supported"), _reply("Add dark mode")]
        title = asyncio.run(
            generate_session_title({"model": "m"}, "add dark mode", "done")
        )
        check(
            "retries without thinking block",
            title == "Add dark mode" and len(fake.calls) == 2,
            repr((title, len(fake.calls))),
        )
        check("second attempt drops extra_body", "extra_body" not in fake.calls[1])

        fake.calls.clear()
        fake.responses = [RuntimeError("boom"), RuntimeError("boom")]
        check(
            "both attempts failing -> None",
            asyncio.run(generate_session_title({"model": "m"}, "hi", "yo")) is None,
        )

        check(
            "no model -> None",
            asyncio.run(generate_session_title({}, "hi", "yo")) is None,
        )

        fake.calls.clear()
        fake.responses = [_reply(""), _reply("  ")]
        check(
            "empty replies -> None",
            asyncio.run(generate_session_title({"model": "m"}, "hi", "yo")) is None,
        )
    finally:
        openai.AsyncOpenAI = original_client

    # ---- static guards ----
    with open(os.path.join(ROOT, "gateway", "server.py"), encoding="utf-8") as f:
        server_src = f.read()
    check("first-50-chars naming removed", "text[:50]" not in server_src)
    check(
        "generated title applied after completion",
        "_apply_generated_title(sid, text, full_content" in server_src,
    )
    check("placeholder guard present", "_PLACEHOLDER_TITLE" in server_src)
    check(
        "skips interrupted turns",
        "if not was_interrupted:\n                    # Runs after the reply is persisted" in server_src,
    )

    with open(os.path.join(ROOT, "gateway", "session.py"), encoding="utf-8") as f:
        session_src = f.read()
    check(
        "compare-and-set title write",
        "def update_title_if_current" in session_src and "AND title=?" in session_src,
    )

    # ---- gateway integration (temp PEBBLE_HOME) ----
    import gateway.server as srv

    async def _flow():
        out = {}
        created = srv._session_store.create(project_path="")
        sid = created["session_id"]
        calls = {"n": 0}

        async def fake_generate(llm_config, user_text, assistant_text, session_id=""):
            calls["n"] += 1
            calls["session_id"] = session_id
            return "登录问题排查"

        original = srv.generate_session_title
        srv.generate_session_title = fake_generate
        events = []

        def write_event(event_type, payload):
            events.append((event_type, payload))

        try:
            await srv._apply_generated_title(sid, "登录一直报错", "我看看", {"model": "m"}, write_event)
            out["title"] = srv._session_store.get(sid)["title"]
            out["events"] = events
            out["calls_after_first"] = calls["n"]

            await srv._apply_generated_title(sid, "登录一直报错", "我看看", {"model": "m"}, write_event)
            out["calls_after_second"] = calls["n"]

            sid2 = srv._session_store.create(project_path="")["session_id"]
            srv._session_store.update_title(sid2, "用户自己起的名")
            await srv._apply_generated_title(sid2, "别的话题", "好", {"model": "m"}, write_event)
            out["manual_title"] = srv._session_store.get(sid2)["title"]
            out["calls_after_manual"] = calls["n"]
        finally:
            srv.generate_session_title = original
        return out

    flow = asyncio.run(_flow())
    check("integration: title persisted", flow["title"] == "登录问题排查", repr(flow["title"]))
    check(
        "integration: session.updated pushed",
        bool(flow["events"])
        and flow["events"][0][0] == "session.updated"
        and flow["events"][0][1].get("title") == "登录问题排查"
        and flow["events"][0][1].get("session_id"),
        repr(flow["events"]),
    )
    check(
        "integration: later turn skips model call",
        flow["calls_after_first"] == 1 and flow["calls_after_second"] == 1,
        repr((flow["calls_after_first"], flow["calls_after_second"])),
    )
    check(
        "integration: manual rename wins",
        flow["manual_title"] == "用户自己起的名" and flow["calls_after_manual"] == 1,
        repr((flow["manual_title"], flow["calls_after_manual"])),
    )

    print()
    if failed:
        print(f"FAILED: {failed} / {passed + failed}")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
