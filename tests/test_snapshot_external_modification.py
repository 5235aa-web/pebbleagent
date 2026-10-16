#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Snapshot revert: external-modification detection (2026-10-17, review issue 6).

Before: conflict phase one treated ANY "current SHA != backup SHA" as normal
("will_overwrite") — content changed after the revert point by another program
or by hand was silently overwritten.  external_modification was defined but
never produced.

Now: after each write tool the snapshot manager records the write-result SHA
(write_result_sha256).  Revert detection treats the current content as safe only
if it is inside the file's known set (all backup SHA ∪ all write-result SHA);
otherwise an external_modification conflict is raised.  Records without
write-result data (legacy) keep the old behavior.

Run: python tests/test_snapshot_external_modification.py   (exit 0 = pass)
"""
import asyncio
import os
import shutil
import sys
import tempfile

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
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name} {detail}")

    from agent_core.snapshot.snapshot_manager import (
        get_snapshot_manager,
        dispose_snapshot_manager,
    )

    tmpdir = tempfile.mkdtemp(prefix="pebble_snap_test_")
    session = "test-session-extmod-001"

    def write(path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def external_types(report):
        return [c.get("conflict_type") for c in report.get("conflicts", [])]

    try:
        manager = get_snapshot_manager(session, tmpdir)
        check("manager created", manager is not None)
        if manager is None:
            raise RuntimeError("no manager")

        f_path = os.path.join(tmpdir, "f.txt")

        # ---- Scenario 1: single write, no external change -> safe ------------
        write(f_path, "A")
        asyncio.run(manager.create_snapshot(
            "write_file", {"file_path": f_path, "content": "B"}, message_id=10, tool_call_id="tc1"))
        write(f_path, "B")  # simulated tool write
        asyncio.run(manager.record_write_result(
            "write_file", {"file_path": f_path}, message_id=10, tool_call_id="tc1"))

        report = manager.detect_conflicts(10)
        check("S1: AI-written content -> no external_modification",
              "external_modification" not in external_types(report),
              f"types={external_types(report)}")

        # ---- Scenario 2: second write in the chain -> still safe -------------
        asyncio.run(manager.create_snapshot(
            "write_file", {"file_path": f_path, "content": "C"}, message_id=11, tool_call_id="tc2"))
        write(f_path, "C")
        asyncio.run(manager.record_write_result(
            "write_file", {"file_path": f_path}, message_id=11, tool_call_id="tc2"))
        report = manager.detect_conflicts(10)
        check("S2: multi-record chain, latest AI content -> no conflict",
              "external_modification" not in external_types(report),
              f"types={external_types(report)}")

        # ---- Scenario 3: external modification -> conflict -------------------
        write(f_path, "D-external")  # changed by "another program"
        report = manager.detect_conflicts(10)
        check("S3: external change -> external_modification conflict",
              "external_modification" in external_types(report),
              f"types={external_types(report)}")
        check("S3: report.has_conflicts is True", report.get("has_conflicts") is True)
        ext = [c for c in report.get("conflicts", []) if c.get("conflict_type") == "external_modification"]
        check("S3: conflict carries paths", ext and ext[0].get("relative_path") == "f.txt",
              f"conflicts={report.get('conflicts')}")

        # ---- Scenario 4: legacy record without write-result -> old behavior --
        g_path = os.path.join(tmpdir, "g.txt")
        write(g_path, "X")
        asyncio.run(manager.create_snapshot(
            "write_file", {"file_path": g_path, "content": "Y"}, message_id=12, tool_call_id="tc3"))
        write(g_path, "Y")
        # no record_write_result (legacy-style data)
        report = manager.detect_conflicts(10)
        g_conflicts = [c for c in report.get("conflicts", [])
                       if c.get("relative_path") == "g.txt"
                       and c.get("conflict_type") == "external_modification"]
        check("S4: legacy (no write-result) keeps compatible behavior",
              not g_conflicts, f"got {g_conflicts}")

        # ---- Scenario 5: known-set collection ---------------------------------
        known, has_wr = manager.collect_known_sha256("f.txt")
        check("S5: known set has backup+result SHAs", has_wr and len(known) >= 3,
              f"known={len(known)} has_wr={has_wr}")
        known_g, has_wr_g = manager.collect_known_sha256("g.txt")
        check("S5: legacy file flagged as no-write-result", not has_wr_g,
              f"has_wr={has_wr_g}")

        # ---- Scenario 6: copy_file is snapshotted now (issue 7e) --------------
        dest = os.path.join(tmpdir, "copied.txt")
        src = os.path.join(tmpdir, "src.txt")
        write(src, "SRC")
        asyncio.run(manager.create_snapshot(
            "copy_file", {"source": src, "destination": dest}, message_id=13, tool_call_id="tc4"))
        write(dest, "SRC")  # simulated copy
        asyncio.run(manager.record_write_result(
            "copy_file", {"source": src, "destination": dest}, message_id=13, tool_call_id="tc4"))
        report = manager.detect_conflicts(13)
        check("S6: copy_file creates a snapshot record (no external conflict for its own write)",
              "external_modification" not in external_types(report),
              f"types={external_types(report)}")

        # ---- Static guards ----------------------------------------------------
        def read(path):
            with open(os.path.join(ROOT, path), "r", encoding="utf-8") as fh:
                return fh.read()

        conflict_src = read("agent_core/snapshot/snapshot_conflict.py")
        check("static: external_modification produced in conflict detector",
              "conflict_type='external_modification'" in conflict_src)
        check("static: known-set consulted before overwrite",
              "collect_known_sha256" in conflict_src)
        exec_src = read("agent_core/tool_executor_impl.py")
        check("static: write-result hook wired into write tools",
              "_snapshot_record_write_result" in exec_src)
        check("static: copy_file included in snapshot conditions",
              "'move_file', 'copy_file'" in exec_src)

    finally:
        try:
            dispose_snapshot_manager(session)
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
