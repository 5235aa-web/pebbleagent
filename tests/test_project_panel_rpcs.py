#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Project panel RPC tests: tree root, corrupt passthrough, add identity, visibility.

Run: python tests/test_project_panel_rpcs.py   (exit 0 = pass)
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_project_panel_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import gateway.server as sv  # noqa: E402
from gateway.session import SessionStore, _get_db  # noqa: E402

REGISTRY = sv._PROJECTS_PATH

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


class FakeService:
    """Only what the path guard and set_project_path touch."""

    def __init__(self):
        self.path = ""
        self._current_project_path = ""

    def set_project_path(self, path):
        self.path = path
        self._current_project_path = path


def reset_registry():
    for suffix in ("", ".bak", ".corrupt"):
        p = REGISTRY.with_name(REGISTRY.name + suffix)
        if p.exists():
            p.unlink()


def names(res):
    result = (res.get("result") or {}) if isinstance(res, dict) else {}
    return [str(row.get("name") or "") for row in (result.get("results") or [])]


def result_of(res):
    return (res.get("result") or {}) if isinstance(res, dict) else {}


def error_of(res):
    return (res.get("error") or {}) if isinstance(res, dict) else {}


def main() -> int:
    a = os.path.join(TMP, "alpha")
    b = os.path.join(TMP, "beta")
    os.makedirs(a, exist_ok=True)
    os.makedirs(b, exist_ok=True)
    with open(os.path.join(a, "probe.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    with open(os.path.join(b, "other.txt"), "w", encoding="utf-8") as f:
        f.write("x")

    original_service = sv._ensure_service
    fake = FakeService()
    sv._ensure_service = lambda: fake
    try:
        # ---- the tree root is the active project, never the program root ----
        reset_registry()
        r = sv.handle_request("file.search", {"q": "", "limit": 50}, "1")
        check("no active project answers an error",
              error_of(r).get("message") == "no active project" or "no active project" in str(result_of(r).get("error")),
              repr(r))
        check("no active project lists nothing", names(r) == [], repr(names(r)))

        r = sv.handle_request("project.add", {"path": a}, "2")
        check("add succeeds", bool(result_of(r).get("projects")), repr(r))
        sv.handle_request("project.set_active", {"path": a}, "3")

        r = sv.handle_request("file.search", {"q": "", "limit": 50}, "4")
        check("file.search falls back to the active project", "probe.txt" in names(r), repr(names(r)))
        check("file.search does not list another project", "other.txt" not in names(r), repr(names(r)))

        r = sv.handle_request("file.search", {"q": "", "path": b, "limit": 50}, "5")
        check("an explicit path is still refused outside the project", names(r) == [], repr(names(r)))
        check("the refusal carries a reason", bool(result_of(r).get("error")), repr(result_of(r)))

        # ---- project.add reports the path it stored ----
        r = sv.handle_request("project.add", {"path": a + os.sep}, "6")
        check("add answers the stored path",
              result_of(r).get("added") == sv._norm_project_path(a), repr(result_of(r).get("added")))
        r = sv.handle_request("project.add", {"path": a}, "7")
        check("re-adding the same project answers the same path",
              result_of(r).get("added") == sv._norm_project_path(a), repr(result_of(r).get("added")))

        # ---- project.list says whether the registry could be read ----
        r = sv.handle_request("project.list", {}, "8")
        check("a readable registry is not reported as corrupt",
              result_of(r).get("corrupt") is False, repr(result_of(r)))

        with open(REGISTRY, "w", encoding="utf-8") as f:
            f.write("{broken")
        r = sv.handle_request("project.list", {}, "9")
        check("a corrupt registry is reported",
              result_of(r).get("corrupt") is True, repr(result_of(r)))
        check("the corrupt copy location is reported",
              str(result_of(r).get("backup") or "").endswith("projects.json.corrupt"),
              repr(result_of(r).get("backup")))
        check("a corrupt registry lists no projects",
              result_of(r).get("projects") == [], repr(result_of(r).get("projects")))
    finally:
        sv._ensure_service = original_service

    # ---- sessions stored under a different spelling stay visible ----
    reset_registry()
    store = SessionStore()
    exact = store.create(title="exact", project_path=a)
    # A row written by an older build: forward slashes plus a trailing separator.
    legacy_path = a.replace("\\", "/") + "/"
    legacy = store.create(title="legacy", project_path="")
    conn = _get_db()
    conn.execute("UPDATE sessions SET project_path=? WHERE id=?", (legacy_path, legacy["session_id"]))
    conn.commit()

    rows = store.list(200, project_path=a)
    ids = [r.get("id") for r in rows]
    check("exact match still lists the row", exact["session_id"] in ids, repr(ids))
    check("a differently spelled row is found too", legacy["session_id"] in ids, repr(ids))

    other = store.create(title="other", project_path=b)
    rows = store.list(200, project_path=a)
    ids = [r.get("id") for r in rows]
    check("another project's sessions stay out", other["session_id"] not in ids, repr(ids))

    print()
    if failed:
        print(f"FAILED: {passed} passed, {failed} failed")
        return 1
    print(f"PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
