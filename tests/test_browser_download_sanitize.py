#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""browser_download filename sanitization.

Covers: unit matrix for _sanitize_download_filename + integration of
_tool_browser_download with a fake httpx client (no network, temp project dir),
plus static guards so the protections cannot be silently removed.

Run: python tests/test_browser_download_sanitize.py   (exit 0 = pass)
"""
import asyncio
import os
import shutil
import sys
import tempfile
import types

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

    import agent_core.tool_executor_impl as tei
    from agent_core.tool_executor_impl import (
        _sanitize_download_filename,
        AgentToolExecutorImpl,
    )

    # ---- Unit: sanitizer allow matrix ---------------------------------------
    cases_allow = {
        "a.zip": "a.zip",
        "sub/a.zip": "sub/a.zip",
        "sub\\a.zip": "sub/a.zip",
        "a//b.zip": "a/b.zip",
        "./a.zip": "a.zip",
        "a/b/c.zip": "a/b/c.zip",
        "CONS.txt": "CONS.txt",
        "中文名.zip": "中文名.zip",
    }
    for raw, want in cases_allow.items():
        got = _sanitize_download_filename(raw)
        check(f"allow {raw!r} -> {want!r}", got == want, f"got {got!r}")

    # ---- Unit: sanitizer deny matrix ----------------------------------------
    cases_deny = [
        "", "   ", ".", "..",
        "../evil.zip", "..\\evil.zip", "a/../../evil.zip", "a/..",
        "/etc/passwd", "/abs.zip", "//server/share/x.zip",
        "C:\\evil.zip", "C:/evil.zip", "c:evil.zip",
        "a\x00b.zip", "a\x01.zip",
        "CON", "con.txt", "NUL", "COM1", "LPT9", "aux.zip", "PRN.dat",
    ]
    for raw in cases_deny:
        got = _sanitize_download_filename(raw)
        check(f"deny {raw!r}", got == "", f"got {got!r}")

    # ---- Integration: _tool_browser_download with fake httpx -----------------
    class FakeResp:
        def __init__(self, content=b"data", ctype="application/zip"):
            self.content = content
            self.headers = {"content-type": ctype}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    from agent_core.code_network_guardV2 import reset_all_guards
    import agent_core.tool_impl.web as web_mod

    orig_httpx = web_mod.httpx
    tmpdir = tempfile.mkdtemp(prefix="pebble_dl_test_")
    try:
        web_mod.httpx = types.SimpleNamespace(AsyncClient=FakeClient)
        ex = AgentToolExecutorImpl(project_path=tmpdir)

        def run(filename, url="http://example.com/f.zip"):
            reset_all_guards()  # per-turn counter is a global singleton
            return asyncio.run(ex._tool_browser_download(url, filename))

        # escape attempts -> rejected before any write
        for bad in ("../evil.zip", "..\\evil.zip", "C:\\abs.zip", "/abs.zip"):
            r = run(bad)
            check(f"download reject {bad!r}",
                  r.startswith("Error: Invalid filename"), f"got: {r[:90]}")

        # url-encoded escape in the URL itself
        r = run("", "http://example.com/..%2Fevil.zip")
        check("download reject url-encoded escape",
              r.startswith("Error: Invalid filename"), f"got: {r[:90]}")

        # legit subdir download -> file lands inside the project
        r = run("sub/ok.zip")
        landed = os.path.join(tmpdir, "sub", "ok.zip")
        check("download subdir ok",
              r.startswith("Download completed") and os.path.isfile(landed),
              f"got: {r[:90]}")

        # no filename -> derived from URL
        r = run("", "http://example.com/pic.png")
        check("download default name from url",
              os.path.isfile(os.path.join(tmpdir, "pic.png")), f"got: {r[:90]}")

        # nothing escaped the temp project dir
        parent_evil = os.path.join(os.path.dirname(tmpdir), "evil.zip")
        check("no escape artifacts outside project", not os.path.exists(parent_evil))

        # ---- Static guards: protections stay wired --------------------------
        src_path = os.path.join(ROOT, "agent_core", "tool_impl", "web.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        check("static: sanitizer wired into download",
              "_sanitize_download_filename(filename)" in src)
        check("static: boundary check wired into download",
              "PathBoundaryChecker.check_path_boundary" in src)
    finally:
        web_mod.httpx = orig_httpx
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
