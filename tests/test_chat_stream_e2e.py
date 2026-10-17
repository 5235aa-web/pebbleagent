#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""chat_stream integration: single turn, tool round, cancel, suspicious retry, usage.

Runs the real chat_stream loop against a fake LLM client and a temporary
PEBBLE_HOME (no network, no production data).

Run: python tests/test_chat_stream_e2e.py   (exit 0 = pass)
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble-chat-")
os.environ["PEBBLE_HOME"] = TMP
os.environ.pop("PEBBLE_CONFIG_DIR", None)
os.environ.pop("PEBBLE_SKIP_PROMPT_HISTORY", None)

import agent_core.chat.turn_executor as turn_executor
from agent_core import agent_chat_history as history
from agent_core.agent_chat_service_v2 import AgentChatServiceV2

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


def chunk(content=None, reasoning=None, tool_calls=None, finish=None, usage=None):
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=tool_calls
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage
    )


def tool_call(index, id_, name, args):
    return SimpleNamespace(
        index=index,
        id=id_,
        function=SimpleNamespace(name=name, arguments=args),
    )


def make_usage(prompt=10, completion=5):
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
    )


class FakeCompletions:
    def __init__(self, rounds, on_chunk=None):
        self.rounds = list(rounds)
        self.calls = []
        self.on_chunk = on_chunk

    async def create(self, **params):
        self.calls.append(params)
        items = self.rounds.pop(0)
        on_chunk = self.on_chunk

        async def gen():
            for i, item in enumerate(items):
                if on_chunk:
                    on_chunk(i)
                yield item

        return gen()


class FakeClient:
    def __init__(self, rounds, on_chunk=None):
        self.completions = FakeCompletions(rounds, on_chunk)
        self.chat = SimpleNamespace(completions=self.completions)


def make_service(holder):
    svc = AgentChatServiceV2()

    async def noop(*args, **kwargs):
        return None

    async def fake_title(*args, **kwargs):
        return "Test title"

    async def fake_execute(tool_name, args, context):
        return "tool ok"

    svc._ensure_mcp_initialized = noop
    svc._extract_memories_async = noop
    svc.generate_conversation_title = fake_title
    svc._get_llm_config = lambda agent_name, model: {
        "api_key": "test",
        "base_url": "http://127.0.0.1:1",
        "model": "test-model",
        "temperature": None,
        "top_p": None,
    }
    svc._load_agent_model_config = lambda agent_name: {}
    svc._load_path_whitelist = lambda: []
    svc._calculate_cost = lambda model, usage: {
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "total_cost": 0.0,
        "period": "offpeak",
    }
    svc._accumulate_cost = lambda conv, info, model_name=None: {"total_cost": 0.0}
    svc._tool_executor.execute = fake_execute

    turn_executor.AsyncOpenAI = lambda **kwargs: holder["client"]
    return svc


async def collect(svc, uuid, text):
    out = []
    async for ev in svc.chat_stream("code_agent", text, uuid, project_path=TMP):
        out.append(ev)
    return out


def kinds(events):
    return [e.get("type") for e in events]


async def case_single_turn(holder):
    print("\n=== single turn, no tools ===")
    client = FakeClient([[chunk(content="Hello"), chunk(finish="stop")]])
    holder["client"] = client
    svc = make_service(holder)
    events = await collect(svc, "conv-single", "hi")
    ks = kinds(events)
    check("chat_started first", ks[0] == "chat_started", ks)
    check("content event", any(e["type"] == "content" and "Hello" in e.get("content", "") for e in events), ks)
    check("chat_completed last", ks[-1] == "chat_completed", ks)
    check("no error event", "error" not in ks, [e for e in events if e["type"] == "error"])
    conv = history.get_agent_conversation_by_uuid("conv-single")
    msgs = history.get_agent_chat_messages_for_context(conv["id"], limit=10) if conv else []
    check("assistant saved", any(m.get("role") == "assistant" and "Hello" in (m.get("content") or "") for m in msgs), msgs)


async def case_tool_round(holder):
    print("\n=== one tool round ===")
    tc = [tool_call(0, "call_1", "read_file", '{"file_path": "a.txt"}')]
    client = FakeClient([
        [chunk(tool_calls=tc, finish="tool_calls")],
        [chunk(content="done"), chunk(finish="stop")],
    ])
    holder["client"] = client
    svc = make_service(holder)
    events = await collect(svc, "conv-tool", "read a.txt")
    ks = kinds(events)
    check("two model calls", len(client.completions.calls) == 2, len(client.completions.calls))
    check("tool event emitted", any(k and k.startswith("tool") for k in ks), ks)
    check("completed", ks[-1] == "chat_completed", ks)
    check("no error", "error" not in ks, [e for e in events if e["type"] == "error"])
    completed = [e for e in events if e["type"] == "chat_completed"]
    check("execution_order has tool", bool(completed) and any(i.get("type") == "tool" for i in completed[-1].get("execution_order") or []), completed[-1].get("execution_order") if completed else None)


async def case_cancel(holder):
    print("\n=== cancel mid-stream ===")
    uuid = "conv-cancel"
    box = {}

    def on_chunk(i):
        if i == 1:
            svc = box.get("svc")
            if svc and uuid in svc._cancel_signals:
                svc._cancel_signals[uuid].set()

    client = FakeClient(
        [[chunk(content="part1"), chunk(content="part2"), chunk(finish="stop")]],
        on_chunk=on_chunk,
    )
    holder["client"] = client
    svc = make_service(holder)
    box["svc"] = svc
    events = await collect(svc, uuid, "cancel me")
    ks = kinds(events)
    check("cancelled event", "cancelled" in ks, ks)
    check("completed with interrupted", bool(events) and ks[-1] == "chat_completed" and events[-1].get("interrupted") is True, events[-1] if events else None)


async def case_suspicious_retry(holder):
    print("\n=== suspicious end -> one retry ===")
    client = FakeClient([
        [chunk(finish=None)],
        [chunk(content="recovered"), chunk(finish="stop")],
    ])
    holder["client"] = client
    svc = make_service(holder)
    events = await collect(svc, "conv-retry", "retry me")
    ks = kinds(events)
    check("two model calls", len(client.completions.calls) == 2, len(client.completions.calls))
    check("recovered content", any(e["type"] == "content" and "recovered" in e.get("content", "") for e in events), ks)
    check("completed", ks[-1] == "chat_completed", ks)
    check("no error", "error" not in ks, [e for e in events if e["type"] == "error"])


async def case_usage(holder):
    print("\n=== usage accounting ===")
    client = FakeClient([[chunk(content="x"), chunk(finish="stop", usage=make_usage())]])
    holder["client"] = client
    svc = make_service(holder)
    events = await collect(svc, "conv-usage", "count tokens")
    ks = kinds(events)
    check("usage event", "usage" in ks, ks)
    check("no error", "error" not in ks, [e for e in events if e["type"] == "error"])


async def main():
    holder = {"client": None}
    await case_single_turn(holder)
    await case_tool_round(holder)
    await case_cancel(holder)
    await case_suspicious_retry(holder)
    await case_usage(holder)
    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
