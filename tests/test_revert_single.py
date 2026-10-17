#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Single-file revert: exact path matching and failure rollback.

Run: python tests/test_revert_single.py   (exit 0 = pass)
"""
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gateway.server as srv
from gateway.rpc import session as session_rpc
from gateway.rpc.context import RpcContext
from agent_core.snapshot import snapshot_manager as sm
from agent_core.snapshot import snapshot_executor as sx

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


def record(rel, abs_path, rid="r1", status="active"):
    return SimpleNamespace(
        relative_path=rel,
        absolute_path=abs_path,
        record_id=rid,
        revert_status=status,
        operation_type="modify",
        backup_path="",
    )


class FakeManager:
    def __init__(self, project_path, records):
        self.project_path = project_path
        self.records = records
        self._file_revert_lock = threading.Lock()
        self.reverting = []
        self.reverted = []
        self.reactivated = []

    def query_all_active(self):
        return [r for r in self.records if r.revert_status == "active"]

    def mark_records_as_reverting(self, ids):
        self.reverting.extend(ids)
        for r in self.records:
            if r.record_id in ids:
                r.revert_status = "reverting"

    def mark_records_as_reverted(self, ids, tag):
        self.reverted.extend(ids)
        for r in self.records:
            if r.record_id in ids:
                r.revert_status = "reverted"

    def mark_records_as_active(self, ids):
        self.reactivated.extend(ids)
        for r in self.records:
            if r.record_id in ids:
                r.revert_status = "active"


def run_revert(manager, executor_cls, relative_path):
    old_get = sm.get_snapshot_manager
    old_exec = sx.RevertExecutor
    sm.get_snapshot_manager = lambda sid, pp=None: manager
    sx.RevertExecutor = executor_cls
    try:
        ctx = RpcContext(
            srv=srv,
            rid="r1",
            params={"session_id": "sess-revert-single", "relative_path": relative_path},
            llm_config={},
            active_model="test-model",
        )
        return session_rpc.handle(ctx, "session.file_revert")
    finally:
        sm.get_snapshot_manager = old_get
        sx.RevertExecutor = old_exec


def main():
    with tempfile.TemporaryDirectory() as tmp:
        # ── 路径匹配：只做完整路径相等 ──
        target = srv._resolve_snapshot_path("a/b.py", tmp)
        check("relative path resolves",
              target == srv._resolve_snapshot_path(os.path.join(tmp, "a", "b.py"), tmp))
        check("absolute input resolves",
              srv._resolve_snapshot_path(os.path.join(tmp, "a", "b.py"), tmp) == target)
        check("dot segments normalize",
              srv._resolve_snapshot_path("a/../a/b.py", tmp) == target)
        check("exact record matches",
              srv._snapshot_record_matches(
                  record("a/b.py", os.path.join(tmp, "a", "b.py")), target, tmp))
        check("suffix path does not match",
              not srv._snapshot_record_matches(
                  record("a/b.py.bak", os.path.join(tmp, "a", "b.py.bak")), target, tmp))
        check("same basename in other dir does not match",
              not srv._snapshot_record_matches(
                  record("x/b.py", os.path.join(tmp, "x", "b.py")), target, tmp))
        check("config does not match config.json",
              not srv._snapshot_record_matches(
                  record("config.json", os.path.join(tmp, "config.json")),
                  srv._resolve_snapshot_path("config", tmp), tmp))

        # ── 失败：记录回滚为 active，返回失败原因 ──
        class FailingExecutor:
            def __init__(self, manager):
                pass

            def _restore_single_file(self, rec):
                return SimpleNamespace(status="failed", message="备份文件不存在")

        failing_manager = FakeManager(
            tmp, [record("a/b.py", os.path.join(tmp, "a", "b.py"), "r1")])
        resp = run_revert(failing_manager, FailingExecutor, "a/b.py")
        result = resp["result"]
        check("failure returns success=false", result["success"] is False)
        check("failure returns message", result["message"] == "备份文件不存在")
        check("failure rolls record back to active",
              failing_manager.reactivated == ["r1"])
        check("failure leaves record active",
              failing_manager.records[0].revert_status == "active")

        # ── 成功：一次退一条，remaining 为剩余版本数 ──
        class OkExecutor:
            def __init__(self, manager):
                pass

            def _restore_single_file(self, rec):
                return SimpleNamespace(status="success", message="已恢复 " + rec.relative_path)

        ok_manager = FakeManager(
            tmp,
            [
                record("a/b.py", os.path.join(tmp, "a", "b.py"), "r2"),
                record("a/b.py", os.path.join(tmp, "a", "b.py"), "r1"),
            ],
        )
        resp = run_revert(ok_manager, OkExecutor, "a/b.py")
        result = resp["result"]
        check("success returns success=true", result["success"] is True)
        check("success reverts newest record first", ok_manager.reverted == ["r2"])
        check("success reports remaining=1", result["remaining"] == 1)
        check("success leaves older record active",
              ok_manager.records[1].revert_status == "active")

        # ── 路径不匹配：不触碰任何记录 ──
        untouched_manager = FakeManager(
            tmp, [record("a/b.py", os.path.join(tmp, "a", "b.py"), "r1")])
        resp = run_revert(untouched_manager, OkExecutor, "a/b.py.bak")
        check("non-matching path returns failure",
              resp["result"]["success"] is False)
        check("non-matching path touches nothing",
              untouched_manager.reverting == [] and untouched_manager.reverted == [])

    print()
    if failed == 0:
        print(f"RESULT: PASS={passed} FAIL=0")
        return 0
    print(f"RESULT: PASS={passed} FAIL={failed}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
