#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ToolOrchestrator end-to-end regression guard.

Runs the real ToolOrchestrator against a fake executor and asserts the full
parallel / serial / mixed / failure / cancel matrix, plus a static scan that
no dangling ``context.state`` reference can reappear.

Run: python tests/test_tool_orchestrator_e2e.py   (exit 0 = pass)
"""
import asyncio
import dataclasses
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class FakeExecutor:
    """Records calls; returns a canned result (or raises) without touching disk."""

    def __init__(self, result="ok", delay=0.0, raise_exc=None):
        self.calls = []
        self.result = result
        self.delay = delay
        self.raise_exc = raise_exc

    async def execute(self, tool_name, args, context):
        self.calls.append((tool_name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        return self.result


def make_context(cancel=False):
    from agent_core.query_state_machine import ToolExecutionContext

    ctx = ToolExecutionContext(
        conversation_uuid="test-conv",
        agent_name="test-agent",
        project_path=ROOT,
        cancel_event=asyncio.Event(),
    )
    if cancel:
        ctx.cancel_event.set()
    return ctx


def run_batch(orchestrator, blocks, context):
    async def _run():
        updates = []
        async for u in orchestrator.run_tools(blocks, context):
            updates.append(u)
        return updates

    return asyncio.run(_run())


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

    from agent_core.tool_orchestrator import ToolOrchestrator
    from agent_core.query_state_machine import ToolUseBlock

    # ---- 1. Parallel batch (concurrency-safe tools) -------------------------
    ex = FakeExecutor()
    orch = ToolOrchestrator(tool_executor=ex)
    blocks = [
        ToolUseBlock(id="c1", name="read_file", arguments={"file_path": "a.py"}),
        ToolUseBlock(id="c2", name="grep", arguments={"pattern": "x"}),
    ]
    updates = run_batch(orch, blocks, make_context())
    events = [u.event for u in updates]
    results = [u.result for u in updates if u.result]
    statuses = sorted(e.status for e in events)
    check("parallel: 4 events (2 running + 2 completed)",
          len(events) == 4 and statuses == ["completed", "completed", "running", "running"],
          f"got {len(events)} events, statuses={statuses}")
    check("parallel: 2 results", len(results) == 2, f"got {len(results)}")
    check("parallel: both tools executed",
          sorted(n for n, _ in ex.calls) == ["grep", "read_file"], f"calls={ex.calls}")
    check("parallel: event fields intact",
          all(e.tool_execution_id and e.tool_name and e.input for e in events))
    check("parallel: complete events carry output/duration",
          all(e.output == "ok" and e.duration is not None
              for e in events if e.status == "completed"))

    # ---- 2. Serial batch (write tools) --------------------------------------
    ex = FakeExecutor()
    orch = ToolOrchestrator(tool_executor=ex)
    blocks = [
        ToolUseBlock(id="s1", name="write_file", arguments={"file_path": "a.py"}),
        ToolUseBlock(id="s2", name="edit_file", arguments={"file_path": "a.py"}),
    ]
    updates = run_batch(orch, blocks, make_context())
    results = [u.result for u in updates if u.result]
    check("serial: 2 results in order",
          [r.tool_name for r in results] == ["write_file", "edit_file"],
          f"got {[r.tool_name for r in results]}")

    # ---- 3. Mixed batch (serial + parallel partitions) ----------------------
    ex = FakeExecutor()
    orch = ToolOrchestrator(tool_executor=ex)
    blocks = [
        ToolUseBlock(id="m1", name="todo_write", arguments={"todos": []}),
        ToolUseBlock(id="m2", name="read_file", arguments={"file_path": "a.py"}),
    ]
    updates = run_batch(orch, blocks, make_context())
    results = [u.result for u in updates if u.result]
    check("mixed: both partitions complete", len(results) == 2, f"got {len(results)}")

    # ---- 4. Tool returning Error: -> failed status ---------------------------
    ex = FakeExecutor(result="Error: boom")
    orch = ToolOrchestrator(tool_executor=ex)
    updates = run_batch(orch, [ToolUseBlock(id="f1", name="read_file", arguments={})],
                        make_context())
    results = [u.result for u in updates if u.result]
    check("failed result: status=failed", len(results) == 1 and results[0].status == "failed")

    # ---- 5. Executor exception -> failed event (chain must not be swallowed) -
    ex = FakeExecutor(raise_exc=RuntimeError("executor crash"))
    orch = ToolOrchestrator(tool_executor=ex)
    updates = run_batch(orch, [ToolUseBlock(id="e1", name="read_file", arguments={})],
                        make_context())
    results = [u.result for u in updates if u.result]
    check("executor exception: failed event produced",
          len(results) == 1 and results[0].status == "failed"
          and "executor crash" in (results[0].error or ""),
          f"got {[(r.status, r.error) for r in results]}")

    # ---- 6. Pre-set cancel: serial path must not execute ---------------------
    ex = FakeExecutor()
    orch = ToolOrchestrator(tool_executor=ex)
    updates = run_batch(orch, [ToolUseBlock(id="x1", name="write_file", arguments={})],
                        make_context(cancel=True))
    check("cancel: serial executor not called", len(ex.calls) == 0, f"calls={ex.calls}")

    # ---- 7. Parallel path survives mid-flight cancel (no crash) --------------
    ex = FakeExecutor(delay=0.3)
    orch = ToolOrchestrator(tool_executor=ex)

    async def _cancel_soon(ctx):
        await asyncio.sleep(0.05)
        ctx.cancel_event.set()

    async def _run_parallel_cancel():
        ctx = make_context()
        task = asyncio.create_task(_cancel_soon(ctx))
        updates = []
        async for u in orch.run_tools(
            [ToolUseBlock(id="p1", name="read_file", arguments={})], ctx
        ):
            updates.append(u)
        await task
        return updates

    try:
        asyncio.run(_run_parallel_cancel())
        check("parallel cancel: no unhandled exception", True)
    except Exception as e:  # pragma: no cover
        check("parallel cancel: no unhandled exception", False, f"raised {type(e).__name__}: {e}")

    # ---- 8. Static guard: dangling reference must never come back ------------
    src_path = os.path.join(ROOT, "agent_core", "tool_orchestrator.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    check("static: no 'context.state' reference in tool_orchestrator",
          "context.state" not in src)

    from agent_core.query_state_machine import ToolExecutionContext
    field_names = {fld.name for fld in dataclasses.fields(ToolExecutionContext)}
    check("static: ToolExecutionContext has no 'state' field (keep in sync if restored)",
          "state" not in field_names, f"fields={sorted(field_names)}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
