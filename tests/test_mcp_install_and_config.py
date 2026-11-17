#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP bundled config, secret status, manual path, and sha256 download install tests.

Run: python tests/test_mcp_install_and_config.py   (exit 0 = pass)
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_mcp_test_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import agent_core.mcp.mcp_config as mcp_cfg  # noqa: E402
mcp_cfg.DEFAULT_USER_CONFIG_DIR = TMP
mcp_cfg.TRUST_STORE_PATH = os.path.join(TMP, "mcp_trusted.json")

import gateway.server as sv  # noqa: E402

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


def main() -> int:
    try:
        # 1. Bundled .mcp.json & mcp_install.json contain only the 3 expected servers
        with open(os.path.join(ROOT, ".mcp.json"), "r", encoding="utf-8") as f:
            bundled_mcp = json.load(f)
        with open(os.path.join(ROOT, "mcp_install.json"), "r", encoding="utf-8") as f:
            install_cfg = json.load(f)

        expected = {"github", "codebase-memory", "chrome-devtools"}
        check("bundled .mcp.json has exact 3 servers", set((bundled_mcp.get("mcpServers") or {}).keys()) == expected)
        check("mcp_install.json has exact 3 servers", set((install_cfg.get("servers") or {}).keys()) == expected)
        check("mcp_install.json removes uv prereq", "uv" not in (install_cfg.get("prerequisites") or {}))

        cbm = (install_cfg.get("servers") or {}).get("codebase-memory") or {}
        sha = cbm.get("sha256") or {}
        check(
            "codebase-memory pins sha256 for amd64 and arm64",
            isinstance(sha, dict) and len(sha.get("amd64", "")) == 64 and len(sha.get("arm64", "")) == 64,
        )
        check("codebase-memory url pins non-latest release", "/releases/latest" not in str(cbm.get("url", "")))

        # 2. Trust the project .mcp.json in our isolated TMP trust store (simulating
        #    packaged _MEIPASS trust or clicking Approve in the MCP panel)
        r_pre = sv.handle_request("mcp.list", {}, "0")
        pending_proj = ((r_pre or {}).get("result") or {}).get("pending_project_servers") or []
        check("untrusted project .mcp.json appears in pending_project_servers", len(pending_proj) >= 1)

        r_trust = sv.handle_request("mcp.trust_project", {"path": os.path.join(ROOT, ".mcp.json")}, "0b")
        check("mcp.trust_project succeeds", ((r_trust or {}).get("result") or {}).get("ok") is True)

        # 3. mcp.list initial metadata
        r = sv.handle_request("mcp.list", {}, "1")
        servers = {s["name"]: s for s in ((r or {}).get("result") or {}).get("servers", [])}
        check("mcp.list sees 3 servers", set(servers.keys()) == expected, f"got {list(servers.keys())}")
        check("github token initially unconfigured", servers["github"].get("token_configured") is False)
        check("codebase-memory install_type is download", servers["codebase-memory"].get("install_type") == "download")
        check("codebase-memory initially not ready", servers["codebase-memory"].get("ready") is False)
        check("codebase-memory provides default install_dir", bool(servers["codebase-memory"].get("install_dir")))

        # 3. mcp.set_secret updates token_configured and keeps other servers untouched
        r = sv.handle_request(
            "mcp.set_secret",
            {"server": "github", "env_key": "GITHUB_PERSONAL_ACCESS_TOKEN", "value": "ghp_real_test_token_123456"},
            "2",
        )
        check("mcp.set_secret succeeds", ((r or {}).get("result") or {}).get("ok") is True)
        r = sv.handle_request("mcp.list", {}, "3")
        servers = {s["name"]: s for s in ((r or {}).get("result") or {}).get("servers", [])}
        check("github token_configured becomes True", servers["github"].get("token_configured") is True)
        check("no pending user approvals after RPC secret write", ((r or {}).get("result") or {}).get("pending_user_servers") == [])

        # 4. mcp.install_server manual path (both file path and containing directory)
        manual_dir = os.path.join(TMP, "manual_cbm")
        os.makedirs(manual_dir, exist_ok=True)
        fake_exe = os.path.join(manual_dir, "codebase-memory-mcp.exe")
        with open(fake_exe, "wb") as f:
            f.write(b"MZ")

        r = sv.handle_request("mcp.install_server", {"server": "codebase-memory", "path": f'"{manual_dir}"'}, "4")
        res = (r or {}).get("result") or {}
        check("manual directory resolves exe", res.get("ok") is True and res.get("exe_path") == os.path.abspath(fake_exe), f"got {res}")

        r = sv.handle_request("mcp.install_server", {"server": "codebase-memory", "path": os.path.join(TMP, "missing.exe")}, "5")
        res = (r or {}).get("result") or {}
        check("missing manual path returns ok=False with error", res.get("ok") is False and bool(res.get("error")))

        # 5. mcp.install_server npx branch does not raise UnboundLocalError and preserves github token
        r = sv.handle_request("mcp.install_server", {"server": "github"}, "6")
        res = (r or {}).get("result") or {}
        if shutil.which("npx"):
            check("npx install returns ok=True without UnboundLocalError", res.get("ok") is True, f"got {res}")
            r_list = sv.handle_request("mcp.list", {}, "6b")
            s_after = {s["name"]: s for s in ((r_list or {}).get("result") or {}).get("servers", [])}
            check("npx install preserves saved github token", s_after["github"].get("token_configured") is True)
        else:
            check("npx install without node returns ok=False cleanly", res.get("ok") is False and "node" in str(res.get("error", "")).lower())

        # 6. _install_download_server extracts zip to custom dir and verifies sha256
        zip_src = os.path.join(TMP, "sample.zip")
        with zipfile.ZipFile(zip_src, "w") as zf:
            zf.writestr("nested/codebase-memory-mcp.exe", b"dummy-binary-bytes")
        with open(zip_src, "rb") as f:
            zip_sha = hashlib.sha256(f.read()).hexdigest()

        orig_dl = sv._download_file
        captured_urls = []
        try:
            def _fake_dl(url, dest, **kwargs):
                captured_urls.append(url)
                shutil.copyfile(zip_src, dest)

            sv._download_file = _fake_dl
            custom_dir = os.path.join(TMP, "custom_dl_dir")
            arch = sv._detect_arch()
            out_exe = sv._install_download_server(
                "codebase-memory",
                {
                    "url": "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.12.1/codebase-memory-mcp-windows-{arch}.zip",
                    "exe_name": "codebase-memory-mcp.exe",
                    "sha256": {arch: zip_sha},
                },
                install_dir_override=custom_dir,
            )
            check("download url substitutes {arch}", captured_urls and arch in captured_urls[0], f"got {captured_urls}")
            check("download extracts exe to custom dir", os.path.isfile(out_exe) and os.path.dirname(out_exe) == os.path.abspath(custom_dir))

            mismatch_raised = False
            try:
                sv._install_download_server(
                    "codebase-memory",
                    {
                        "url": "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.12.1/codebase-memory-mcp-windows-{arch}.zip",
                        "exe_name": "codebase-memory-mcp.exe",
                        "sha256": {arch: "0" * 64},
                    },
                    install_dir_override=custom_dir,
                )
            except RuntimeError as e:
                mismatch_raised = "sha256 mismatch" in str(e)
            check("sha256 mismatch rejects download", mismatch_raised)
        finally:
            sv._download_file = orig_dl
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
