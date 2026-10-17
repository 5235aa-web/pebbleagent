#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Transcript manager: write, read, search, session lifecycle.

Run: python tests/test_transcript_manager.py   (exit 0 = pass)
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble-transcript-")
os.environ["PEBBLE_HOME"] = TMP
os.environ.pop("PEBBLE_CONFIG_DIR", None)
os.environ.pop("PEBBLE_SKIP_PROMPT_HISTORY", None)

from agent_core.transcript_manager import get_transcript_manager, get_transcript_path

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
    tm = get_transcript_manager()
    tm.disable_persistence(False)
    project = os.path.join(TMP, "proj")
    os.makedirs(project, exist_ok=True)
    session = "sess-a"

    tm.set_session(session, project)
    path = get_transcript_path(session, project)
    check("path under PEBBLE_HOME", path.startswith(TMP), path)

    ok = tm.write_session_transcript_segment([
        {"role": "user", "content": "alpha needle"},
        {"role": "assistant", "content": "beta"},
    ], session, project)
    check("write returns True", ok)
    check("file exists", os.path.exists(path))

    entries = tm.read_transcript(session, project)
    check("read count 2", len(entries) == 2, str(len(entries)))
    check("content roundtrip", any("needle" in json.dumps(e, ensure_ascii=False) for e in entries))

    hits = tm.search_transcript("needle", session, project)
    check("search finds", len(hits) == 1, str(len(hits)))
    check("search case-insensitive", len(tm.search_transcript("NEEDLE", session, project)) == 1)

    check("session exists", tm.session_exists(session))
    check("delete", tm.delete_session(session))
    check("session gone", not tm.session_exists(session))
    check("read after delete empty", tm.read_transcript(session, project) == [])

    tm.reset()
    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
