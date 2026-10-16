#!/usr/bin/env python3
"""Pebble Agent TUI Gateway - stdin/stdout JSON-RPC 2.0 server."""

import json
import logging
import os
import sys
import threading

from .log_setup import setup_logging

setup_logging()
logger = logging.getLogger("gateway")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from .server import handle_request, handle_prompt_submit, _session_store

_stdout_lock = threading.Lock()


def write_json(obj: dict) -> bool:
    try:
        with _stdout_lock:
            line = json.dumps(obj, ensure_ascii=False)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        return True
    except BrokenPipeError:
        return False
    except Exception as e:
        logger.error(f"Write error: {e}")
        return False


def write_event(event_type: str, payload: dict):
    write_json({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": event_type, "payload": payload},
    })


def dispatch(req: dict) -> dict | None:
    method = req.get("method", "")
    params = req.get("params", {}) or {}
    rid = req.get("id")
    if rid is None:
        return None

    result = handle_request(method, params, rid)
    if result is not None:
        return result
    return {"id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def _prompt_worker(sid: str, text: str):
    write_json({"id": f"prompt_{sid}", "result": {"ok": True}})
    handle_prompt_submit(sid, text, write_event)


def main():
    write_json({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "gateway.ready",
            "payload": {"skin": {"banner_logo": "[#ffa08a][bold]Pebble AI[/]"}},
        },
    })

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            write_json({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})
            continue

        method = req.get("method", "")
        params = req.get("params", {}) or {}

        if method == "prompt.submit":
            sid = params.get("session_id", "default")
            text = params.get("text", "")
            if not text:
                write_json({"id": req.get("id"), "result": {"ok": False}})
                continue
            write_json({"id": req.get("id"), "result": {"ok": True}})
            t = threading.Thread(target=_prompt_worker, args=(sid, text), daemon=True)
            t.start()
            continue

        resp = dispatch(req)
        if resp is not None:
            write_json(resp)


if __name__ == "__main__":
    main()
