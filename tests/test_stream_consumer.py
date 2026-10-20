#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""StreamConsumer behaviour.

Covers: reasoning/content accumulation and segment ordering, tool-call chunk
assembly, finish_reason capture, cancellation, and open_stream error mapping.

Run: python tests/test_stream_consumer.py   (exit 0 = pass)
"""
import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core.chat.stream_client import PromptTooLong, StreamConsumer, open_stream
from agent_core.context_compressor import estimate_tokens

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


def chunk(content=None, reasoning=None, tool_calls=None, finish=None):
    delta = types.SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def tc(index, id_=None, name=None, args=None):
    fn = types.SimpleNamespace(name=name, arguments=args)
    return types.SimpleNamespace(index=index, id=id_, function=fn)


async def stream_of(chunks):
    for c in chunks:
        yield c


class Cancel:
    def __init__(self):
        self.flag = False

    def is_set(self):
        return self.flag


async def main():
    # ---- reasoning + content segments ----
    order = []
    consumer = StreamConsumer(order, 0, Cancel())
    events = []
    async for ev in consumer.consume(stream_of([
        chunk(reasoning="think1"),
        chunk(reasoning="think2"),
        chunk(content="hello"),
        chunk(content=" world", finish="stop"),
    ])):
        events.append(ev)

    check("reasoning accumulated", consumer.reasoning == "think1think2")
    check("content accumulated", consumer.content == "hello world")
    check("finish_reason captured", consumer.finish_reason == "stop")
    check("two segments in order", len(order) == 2, f"order={order}")
    check("segment types", [s["type"] for s in order] == ["reasoning", "text"], f"{order}")
    check("segment order indices", [s["order"] for s in order] == [0, 1], f"{order}")
    check("reasoning events emitted", sum(1 for e in events if e["type"] == "reasoning") == 2)
    check("content events emitted", sum(1 for e in events if e["type"] == "content") == 2)

    # ---- live token counter (reasoning + content) ----
    order_t = []
    c_t = StreamConsumer(order_t, 0, Cancel())
    events_t = []
    async for ev in c_t.consume(stream_of([
        chunk(reasoning="thinking about it " * 2),
        chunk(content="hello world " * 3, finish="stop"),
    ])):
        events_t.append(ev)
    toks = [e.get("tokens") for e in events_t if e["type"] in ("reasoning", "content")]
    check("every delta carries cumulative tokens",
          all(isinstance(t, int) and t >= 0 for t in toks), f"{toks}")
    check("token count is monotonic",
          all(b >= a for a, b in zip(toks, toks[1:])), f"{toks}")
    check("token count includes reasoning", c_t.output_tokens > 0, f"{c_t.output_tokens}")
    check("token count matches estimate",
          c_t.output_tokens == estimate_tokens(c_t.reasoning + c_t.content),
          f"{c_t.output_tokens}")

    # ---- tool call assembly ----
    order2 = []
    c2 = StreamConsumer(order2, 0, Cancel())
    async for _ in c2.consume(stream_of([
        chunk(tool_calls=[tc(0, id_="call_1", name="read_file", args='{"file')]),
        chunk(tool_calls=[tc(0, args='_path": "a.txt"}')], finish="tool_calls"),
    ])):
        pass
    check("tool call id", c2.tool_calls_chunks[0]["id"] == "call_1")
    check("tool call name", c2.tool_calls_chunks[0]["function"]["name"] == "read_file")
    check("tool args concatenated",
          c2.tool_calls_chunks[0]["function"]["arguments"] == '{"file_path": "a.txt"}',
          c2.tool_calls_chunks[0]["function"]["arguments"])

    # ---- tool-call arguments count toward output tokens ----
    order3 = []
    c3 = StreamConsumer(order3, 0, Cancel())
    async for _ in c3.consume(stream_of([
        chunk(tool_calls=[tc(0, id_="call_2", name="write_file",
                             args='{"path": "a.txt", "content": "0123456789abcdefghij"}')], finish="tool_calls"),
    ])):
        pass
    check("tool args counted as output tokens", c3.output_tokens > 0, f"{c3.output_tokens}")

    # ---- cancellation ----
    cancel = Cancel()
    c3 = StreamConsumer([], 0, cancel)

    async def cancelling_stream():
        yield chunk(content="a")
        cancel.flag = True
        yield chunk(content="b")

    events3 = [ev async for ev in c3.consume(cancelling_stream())]
    check("cancel marks interrupted", c3.interrupted is True)
    check("cancel event emitted", any(e["type"] == "cancelled" for e in events3))
    check("content before cancel kept", c3.content == "a", c3.content)

    # ---- open_stream ----
    class FakeCompletions:
        def __init__(self, error=None):
            self.error = error
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.error:
                raise self.error
            return "stream-ok"

    class FakeClient:
        def __init__(self, error=None):
            self.chat = types.SimpleNamespace(completions=FakeCompletions(error))

    stream, status, abort = await open_stream(FakeClient(), {}, Cancel())
    check("open_stream success", stream == "stream-ok" and status is None and abort is False)

    class PromptTooLongError(Exception):
        pass

    err = Exception("maximum context length is 1000 tokens, your messages resulted in 1200")
    try:
        await open_stream(FakeClient(err), {}, Cancel())
        check("prompt_too_long raises", False)
    except PromptTooLong as e:
        check("prompt_too_long raises", True)
        check("prompt_too_long message", "上下文长度超出" in str(e), str(e))

    stream2, status2, abort2 = await open_stream(
        FakeClient(Exception("400 invalid assistant message")), {}, Cancel(), in_suspicious_retry=True
    )
    check("suspicious retry rejected -> graceful abort", stream2 is None and abort2 is True)

    cancel2 = Cancel()
    cancel2.flag = True
    stream3, status3, _ = await open_stream(FakeClient(), {}, cancel2)
    check("cancelled before request", stream3 is None and status3 == "cancelled")

    # ---- gateway passthrough (static guard) ----
    with open(os.path.join(ROOT, "gateway", "server.py"), encoding="utf-8") as f:
        server_src = f.read()
    check("message.delta forwards tokens",
          '"message.delta", {"text": chunk, "tokens"' in server_src)
    check("thinking.delta forwards tokens",
          '"thinking.delta", {"text": chunk, "tokens"' in server_src)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
