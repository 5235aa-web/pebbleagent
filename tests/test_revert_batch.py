#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RevertExecutor.restore_batch: conflict policy for session.file_revert_all.

Run: python tests/test_revert_batch.py   (exit 0 = pass)
"""
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


class FakeDetector:
    report = {}

    def __init__(self, manager):
        pass

    def detect(self, message_id):
        return FakeDetector.report


class FakeManager:
    session_uuid = "sess"

    def __init__(self, records):
        self.records = records
        self.reverting = []
        self.reverted = []

    def query_by_message(self, message_id):
        return self.records

    def mark_records_as_reverting(self, ids):
        self.reverting.extend(ids)

    def mark_records_as_reverted(self, ids, tag):
        self.reverted.extend(ids)

    def _atomic_copy(self, src, dst):
        shutil.copy2(src, dst)


def make_record(tmp, name, backup_name):
    backup = os.path.join(tmp, backup_name)
    with open(backup, "w", encoding="utf-8") as f:
        f.write("restored")
    return SimpleNamespace(
        relative_path=name,
        absolute_path=os.path.join(tmp, name),
        operation_type="modify",
        backup_path=backup,
        record_id="r-" + name,
        revert_status="active",
        created_at=0,
    )


def main():
    sx.ConflictDetector = FakeDetector
    tmp = tempfile.mkdtemp(prefix="pebble-revert-")
    a = make_record(tmp, "a.txt", "a.bak")
    b = make_record(tmp, "b.txt", "b.bak")

    FakeDetector.report = {"has_conflicts": True, "conflict_count": 1, "conflicts": [{"relative_path": "a.txt"}]}
    mgr = FakeManager([a, b])
    res = sx.RevertExecutor(mgr).restore_batch(1, "abort")
    check("abort blocks", res["success"] is False and res["restored"] == 0, res)
    check("abort marks nothing", mgr.reverting == [], mgr.reverting)

    mgr = FakeManager([a, b])
    res = sx.RevertExecutor(mgr).restore_batch(1, "skip_conflicts")
    check("skip counts", res["skipped"] == 1 and res["restored"] == 1, res)
    check("skip leaves conflict file", not os.path.exists(a.absolute_path), a.absolute_path)
    check("skip restores other", os.path.exists(b.absolute_path), b.absolute_path)

    FakeDetector.report = {"has_conflicts": False, "conflict_count": 0, "conflicts": []}
    c = make_record(tmp, "c.txt", "c.bak")
    mgr = FakeManager([c])
    res = sx.RevertExecutor(mgr).restore_batch(1, "abort")
    check("clean restores", res["success"] is True and res["restored"] == 1, res)
    check("clean writes file", os.path.exists(c.absolute_path), c.absolute_path)

    mgr = FakeManager([])
    res = sx.RevertExecutor(mgr).restore_batch(1, "abort")
    check("empty reports failure", res["success"] is False and res["restored"] == 0, res)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
