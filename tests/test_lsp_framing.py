#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LSP message framing: Content-Length headers, JSON body, error paths.

Run: python tests/test_lsp_framing.py   (exit 0 = pass)
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core.lsp_client import LspMessageReader

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


async def read_frame(payload: bytes):
    stream = asyncio.StreamReader()
    stream.feed_data(payload)
    stream.feed_eof()
    return await LspMessageReader(stream).read_message()


def frame(body: bytes, extra_headers: bytes = b"") -> bytes:
    return b"Content-Length: " + str(len(body)).encode() + b"\r\n" + extra_headers + b"\r\n" + body


async def main() -> int:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    body = json.dumps(payload).encode()

    msg = await read_frame(frame(body))
    check("parses body", msg == payload, str(msg))

    msg = await read_frame(frame(body, b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"))
    check("ignores extra header", msg == payload, str(msg))

    msg = await read_frame(frame(json.dumps({"method": "textDocument/publishDiagnostics"}).encode()))
    check("parses notification", msg == {"method": "textDocument/publishDiagnostics"}, str(msg))

    msg = await read_frame(b"Content-Length: abc\r\n\r\n")
    check("invalid length -> None", msg is None)

    msg = await read_frame(b"Content-Length: 0\r\n\r\n")
    check("zero length -> None", msg is None)

    msg = await read_frame(b"")
    check("closed stream -> None", msg is None)

    msg = await read_frame(frame(b"{not json"))
    check("bad json -> None", msg is None)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
