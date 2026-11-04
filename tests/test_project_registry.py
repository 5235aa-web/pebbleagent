#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Project registry tests: normalisation, atomic save, corruption recovery.

Run: python tests/test_project_registry.py   (exit 0 = pass)
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_project_registry_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import gateway.server as sv  # noqa: E402

passed = 0
failed = 0

REGISTRY = Path(sv._PROJECTS_PATH)


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


def make_dir(name: str) -> str:
    path = os.path.join(TMP, name)
    os.makedirs(path, exist_ok=True)
    return path


def reset_registry():
    for suffix in ("", ".bak", ".corrupt"):
        p = REGISTRY if not suffix else REGISTRY.with_name(REGISTRY.name + suffix)
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def read_registry_raw():
    with open(REGISTRY, "r", encoding="utf-8") as f:
        return json.load(f)


class FakeService:
    def __init__(self):
        self.path = ""

    def set_project_path(self, path):
        self.path = path


def main() -> int:
    a = make_dir("alpha")
    b = make_dir("beta")

    # ---- path normalisation ----
    check("empty path stays empty", sv._norm_project_path("") == "", repr(sv._norm_project_path("")))
    check("blank path stays empty", sv._norm_project_path("   ") == "")
    check("None path stays empty", sv._norm_project_path(None) == "")
    check("real path is absolute",
          sv._norm_project_path(a) == os.path.normpath(os.path.abspath(a)))

    # ---- add rejects an empty path instead of registering the cwd ----
    reset_registry()
    r = sv.handle_request("project.add", {"path": ""}, "1")
    check("add empty path answers error", bool(r.get("error")), repr(r))
    check("add empty path writes nothing", not REGISTRY.exists())

    r = sv.handle_request("project.add", {"path": "   "}, "2")
    check("add blank path answers error", bool(r.get("error")), repr(r))
    check("add blank path writes nothing", not REGISTRY.exists())

    # ---- add a real directory ----
    r = sv.handle_request("project.add", {"path": a}, "3")
    res = r.get("result") or {}
    check("add returns the project",
          sv._norm_project_path(a) in (res.get("projects") or []), repr(res))
    check("first project becomes active",
          sv._norm_project_path(res.get("active") or "") == sv._norm_project_path(a),
          repr(res.get("active")))
    check("registry file exists", REGISTRY.exists())

    # ---- the second save leaves the previous generation beside the file ----
    sv.handle_request("project.add", {"path": b}, "4")
    backup = REGISTRY.with_name(REGISTRY.name + ".bak")
    check("second save keeps a .bak", backup.exists())
    if backup.exists():
        with open(backup, "r", encoding="utf-8") as f:
            prev = json.load(f)
        check(".bak holds the previous generation",
              sv._norm_project_path(a) in (prev.get("projects") or []), repr(prev))
    check("registry parses after an atomic save",
          len(read_registry_raw().get("projects") or []) == 2)

    # ---- active is normalised before it is compared ----
    reset_registry()
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump({"projects": [a], "active": a + os.sep}, f)
    data = sv._read_projects()
    check("trailing separator does not reselect projects[0]",
          data.get("active") == sv._norm_project_path(a), repr(data.get("active")))
    check("registry keeps the project",
          data.get("projects") == [sv._norm_project_path(a)], repr(data.get("projects")))

    # ---- a corrupt file is preserved, not turned into an empty registry ----
    reset_registry()
    with open(REGISTRY, "w", encoding="utf-8") as f:
        f.write("{not json")
    data = sv._read_projects()
    corrupt = REGISTRY.with_name(REGISTRY.name + ".corrupt")
    check("corrupt read reports the flag", data.get("corrupt") is True, repr(data))
    check("corrupt read returns no projects", data.get("projects") == [], repr(data))
    check("original file is left in place", REGISTRY.exists())
    check("a corrupt copy is saved", corrupt.exists())
    if corrupt.exists():
        check("corrupt copy holds the original bytes",
              corrupt.read_text(encoding="utf-8") == "{not json")

    # ---- a corrupt registry refuses writes instead of solidifying an empty list ----
    r = sv.handle_request("project.add", {"path": a}, "5")
    check("add on a corrupt registry is refused", bool(r.get("error")), repr(r))
    check("refused add leaves the corrupt file untouched",
          REGISTRY.read_text(encoding="utf-8") == "{not json")
    check("refused add keeps the corrupt copy",
          corrupt.exists() and corrupt.read_text(encoding="utf-8") == "{not json")
    check("refused add names the preserved copy",
          "projects.json.corrupt" in str((r.get("error") or {}).get("message", "")), repr(r))

    # ---- a healthy save persists only the two registry keys ----
    reset_registry()
    r = sv.handle_request("project.add", {"path": a}, "6")
    check("add after a clean reset succeeds", bool(r.get("result")), repr(r))
    saved = json.loads(REGISTRY.read_text(encoding="utf-8"))
    check("saved registry carries only active and projects",
          sorted(saved.keys()) == ["active", "projects"], repr(saved))

    # ---- switching is refused while a turn is running ----
    reset_registry()
    sv.handle_request("project.add", {"path": a}, "6")
    sv.handle_request("project.add", {"path": b}, "7")
    original_probe = sv._any_session_running_probe
    original_service = sv._ensure_service
    fake = FakeService()
    try:
        sv._ensure_service = lambda: fake
        sv._any_session_running_probe = lambda: True
        r = sv.handle_request("project.set_active", {"path": b, "session_id": "busy-session"}, "8")
        check("running turn refuses the switch", bool(r.get("error")), repr(r))
        check("refused switch keeps the active project",
              sv._norm_project_path(sv._read_projects().get("active") or "") == sv._norm_project_path(a),
              repr(sv._read_projects().get("active")))
        check("refused switch does not touch the service", fake.path == "")

        # The guard is global: another client's turn moves its tools too, and a
        # request without a session id must not be able to slip past it.
        r = sv.handle_request("project.set_active", {"path": b}, "8b")
        check("refused switch ignores a missing session id", bool(r.get("error")), repr(r))

        # ---- a failed service sync rolls the registry back ----
        def broken_service():
            raise RuntimeError("service unavailable")

        sv._any_session_running_probe = None
        sv._ensure_service = broken_service
        r = sv.handle_request("project.set_active", {"path": b}, "9")
        check("failed sync answers error", bool(r.get("error")), repr(r))
        check("failed sync rolls the active project back",
              sv._norm_project_path(sv._read_projects().get("active") or "") == sv._norm_project_path(a),
              repr(sv._read_projects().get("active")))
    finally:
        sv._any_session_running_probe = original_probe
        sv._ensure_service = original_service

    # ---- session.create records the project path, not the session id ----
    r = sv.handle_request("session.create", {"project_path": a}, "10")
    sid = (r.get("result") or {}).get("session_id") or ""
    mapped = sv._session_project_paths.get(sid)
    check("session.create maps the project path", mapped == sv._norm_project_path(a), repr(mapped))
    check("session.create does not map the session id as a path",
          mapped != sv._norm_project_path(sid), repr(mapped))
    r = sv.handle_request("session.create", {"project_path": ""}, "11")
    sid2 = (r.get("result") or {}).get("session_id") or ""
    check("session.create without a project leaves no mapping",
          sid2 not in sv._session_project_paths)

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
