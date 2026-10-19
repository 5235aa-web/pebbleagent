"""
PebbleAgent GUI Gateway - WebSocket JSON-RPC 2.0 server.

Replaces the stdin/stdout TUI gateway with a WebSocket server
suitable for the GUI frontend.
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import sys
import threading
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gateway.server import handle_request, handle_prompt_submit, set_ws_loop
from gateway.permission_service import get_permission_service

logger = logging.getLogger("ws-gateway")

# ===== Project root (same as gateway.server) =====
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(os.path.expanduser("~"), ".pebble")

# ===== Authentication & limits =====
# Token is generated on first run and persisted to ~/.pebble/gateway.token
# (delete the file to rotate). Every client must present it via the query
# string (?token=...) or the X-Pebble-Token header. Origin is checked as a
# second line of defense for browser clients; non-browser local clients that
# send no Origin header pass the token check only.
_ALLOWED_ORIGINS = {
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://tauri.localhost",
    "tauri://localhost",
}
_MAX_CONNECTIONS = 8
_MAX_MESSAGE_SIZE = 128 * 1024 * 1024  # 128 MiB, replaces the previous max_size=None

# Payload limits (memory-exhaustion guard)
_MAX_TEXT_CHARS = 2 * 1024 * 1024                 # ~2M chars of prompt text
_MAX_ATTACHMENTS = 20                             # aligned with frontend useFileDrop MAX_COUNT
_MAX_ATTACHMENT_B64_CHARS = 14 * 1024 * 1024      # ~10 MiB decoded, aligned with frontend MAX_SIZE
_MAX_TOTAL_ATTACHMENT_B64_CHARS = 100 * 1024 * 1024

_gateway_token: str = ""


def _load_or_create_gateway_token() -> str:
    """Load the persisted gateway token, generating one on first run."""
    os.makedirs(_DATA_HOME, exist_ok=True)
    token_path = os.path.join(_DATA_HOME, "gateway.token")
    try:
        if os.path.exists(token_path):
            with open(token_path, "r", encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
    except Exception as e:
        logger.warning(f"[WS] Failed to read gateway token: {e}")
    token = secrets.token_urlsafe(32)
    try:
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(token)
        try:
            os.chmod(token_path, 0o600)  # best effort on Windows
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[WS] Failed to persist gateway token: {e}")
    return token


def _extract_ws_token(ws) -> str:
    """Extract the auth token from the request (X-Pebble-Token header preferred,
    ?token= query string as fallback for browser clients, which cannot set
    custom headers on WebSocket handshakes)."""
    try:
        request = ws.request
        headers = getattr(request, "headers", None)
        if headers is not None:
            token = headers.get("X-Pebble-Token", "")
            if token:
                return token
        path = getattr(request, "path", "") or ""
        qs = parse_qs(urlparse(path).query)
        return (qs.get("token") or [""])[0]
    except Exception:
        pass
    return ""


def _extract_ws_origin(ws) -> Optional[str]:
    """Extract the Origin header of the request (None for non-browser clients)."""
    try:
        headers = getattr(ws.request, "headers", None)
        if headers is not None:
            return headers.get("Origin")
    except Exception:
        pass
    return None


def _check_payload_limits(text: str, attachments: Any) -> Optional[str]:
    """Return an error message when a prompt payload exceeds the limits, else None."""
    if isinstance(text, str) and len(text) > _MAX_TEXT_CHARS:
        return f"text too large ({len(text)} chars, limit {_MAX_TEXT_CHARS})"
    if not attachments:
        return None
    if not isinstance(attachments, list):
        return "attachments must be a list"
    if len(attachments) > _MAX_ATTACHMENTS:
        return f"too many attachments ({len(attachments)}, limit {_MAX_ATTACHMENTS})"
    total = 0
    for i, att in enumerate(attachments):
        if not isinstance(att, dict):
            return f"attachment[{i}] must be an object"
        b64 = att.get("data_base64") or ""
        if not isinstance(b64, str):
            return f"attachment[{i}].data_base64 must be a string"
        if len(b64) > _MAX_ATTACHMENT_B64_CHARS:
            return f"attachment[{i}] too large ({len(b64)} base64 chars, limit {_MAX_ATTACHMENT_B64_CHARS})"
        total += len(b64)
        text_content = att.get("text_content")
        if isinstance(text_content, str) and len(text_content) > _MAX_TEXT_CHARS:
            return f"attachment[{i}].text_content too large"
    if total > _MAX_TOTAL_ATTACHMENT_B64_CHARS:
        return f"attachments total too large ({total} base64 chars, limit {_MAX_TOTAL_ATTACHMENT_B64_CHARS})"
    return None


# ===== Active connections =====
_connections: set = set()

# ===== Running prompt tasks =====
_prompt_tasks: dict[str, threading.Thread] = {}

class WebSocketWriter:
    """Wraps a websocket connection to provide write_event() compatible with handle_prompt_submit."""

    def __init__(self, ws):
        self.ws = ws
        self._send_failed = False  # 断线后事件会被丢弃：每个请求只记一次日志（防刷屏），前端重连后自行恢复

    def write_event(self, event_type: str, payload: dict):
        """Called from handle_prompt_submit to stream events back."""
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": event_type, "payload": payload},
        }, ensure_ascii=False)
        asyncio.run_coroutine_threadsafe(self._send(msg), _loop)

    async def _send(self, msg: str):
        try:
            await self.ws.send(msg)
        except websockets.ConnectionClosed as e:
            if not self._send_failed:
                self._send_failed = True
                logger.info(f"[WS] Stream events dropped (connection closed, code={getattr(e, 'code', '?')}); frontend will recover from DB on reconnect: {e}")
        except Exception as e:
            if not self._send_failed:
                self._send_failed = True
                logger.warning(f"[WS] Stream send failed: {e}")

_loop: asyncio.AbstractEventLoop | None = None
_mcp_init_task: asyncio.Task | None = None

async def _init_mcp_background():
    """后台初始化 MCP 连接（启动后自动执行）"""
    try:
        from agent_core.mcp.mcp_manager import get_mcp_manager
        from agent_core.mcp.mcp_config import get_mcp_config_manager

        cfg_mgr = get_mcp_config_manager()
        cfg_mgr.set_project_path(str(_PROJECT_ROOT))

        # 用户数据目录 .mcp.json 的覆盖配置（如 UI 填写的 GitHub Token）→ dynamic 层注入
        try:
            from agent_core.mcp.mcp_config import build_user_override_configs
            overrides = build_user_override_configs()
            if overrides:
                cfg_mgr.set_dynamic_configs(overrides)
                logger.info(f"[MCP] Applied {len(overrides)} user override config(s) from ~/.pebble/.mcp.json")
        except Exception as e:
            logger.warning(f"[MCP] Failed to apply user override configs: {e}")

        mgr = get_mcp_manager()
        if not mgr._initialized:
            logger.info("[MCP] Auto-initializing MCP connections on startup...")
            await mgr.initialize()
            logger.info(
                f"[MCP] Auto-init complete: {len(mgr._clients)} servers, "
                f"{len(mgr._tools)} tools"
            )
    except Exception as e:
        logger.warning(f"[MCP] Auto-init deferred (will init on first request): {e}")

async def _verify_audit_integrity_background():
    """启动时后台校验审计日志完整性"""
    try:
        from gateway.permission_audit import get_audit_logger
        ok = await asyncio.to_thread(get_audit_logger().verify_integrity)
        if not ok:
            logger.error("[WS] Permission audit integrity check FAILED — audit log may have been tampered")
    except Exception as e:
        logger.warning(f"[WS] Audit integrity check skipped: {e}")

def _prompt_worker(sid: str, text: str, writer: WebSocketWriter, attachments: list | None = None, project_path: str | None = None):
    """Run handle_prompt_submit in a thread, using the writer for events."""
    import threading
    _sl = getattr(threading, '_pebble_locks', None)
    if _sl is None:
        _sl = {}
        threading._pebble_locks = _sl
    lock = _sl.setdefault(sid, threading.Lock())
    with lock:
        try:
            handle_prompt_submit(sid, text, writer.write_event, attachments, project_path)
        except Exception:
            # 线程崩溃不再静默：完整记录堆栈（曾发生数据库异常导致线程无声死亡）
            logger.exception(f"[WS] _prompt_worker crashed for session {sid}")

async def handle_ws(ws):
    """Handle a single WebSocket connection."""
    global _loop

    # --- Authentication: Origin whitelist (browsers) + mandatory token ---
    origin = _extract_ws_origin(ws)
    if origin is not None and origin not in _ALLOWED_ORIGINS:
        logger.warning(f"[WS] Rejected connection from disallowed origin: {origin}")
        await ws.close(code=4403, reason="origin not allowed")
        return
    presented = _extract_ws_token(ws)
    if not presented or not _gateway_token or not hmac.compare_digest(presented, _gateway_token):
        logger.warning(f"[WS] Rejected unauthenticated connection from {ws.remote_address}")
        await ws.close(code=4401, reason="unauthorized")
        return
    if len(_connections) >= _MAX_CONNECTIONS:
        logger.warning(f"[WS] Rejected connection: too many active connections ({len(_connections)})")
        await ws.close(code=4429, reason="too many connections")
        return

    _loop = asyncio.get_running_loop()

    _connections.add(ws)
    remote = ws.remote_address
    logger.info(f"[WS] Connection from {remote}")

    # Send gateway.ready event
    await ws.send(json.dumps({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "gateway.ready",
            "payload": {"version": "1.0", "mode": "gui"},
        },
    }, ensure_ascii=False))

    try:
        async for raw in ws:
            line = raw.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                    "id": None,
                }, ensure_ascii=False))
                continue

            method = req.get("method", "")
            params = req.get("params", {}) or {}
            rid = req.get("id")

            # Prompt submit starts a streaming chat in a background thread
            if method == "prompt.submit":
                sid = params.get("session_id", "default")
                text = params.get("text", "")
                attachments = params.get("attachments", None)
                project_path = params.get("project_path", None)

                # Payload limits (DoS guard); reject before any further work
                guard_err = _check_payload_limits(text, attachments)
                if guard_err:
                    logger.warning(f"[WS] prompt.submit rejected: {guard_err}")
                    if rid:
                        await ws.send(json.dumps({
                            "id": rid, "result": {"ok": False, "error": guard_err},
                        }, ensure_ascii=False))
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {"type": "prompt.rejected", "payload": {"reason": guard_err}},
                    }, ensure_ascii=False))
                    continue

                if not text and not attachments:
                    if rid:
                        await ws.send(json.dumps({
                            "id": rid, "result": {"ok": False, "error": "empty text"},
                        }, ensure_ascii=False))
                    continue

                # Acknowledge immediately
                if rid:
                    await ws.send(json.dumps({
                        "id": rid, "result": {"ok": True},
                    }, ensure_ascii=False))

                # Start streaming in background
                writer = WebSocketWriter(ws)
                t = threading.Thread(
                    target=_prompt_worker,
                    args=(sid, text, writer, attachments, project_path),
                    daemon=True,
                )
                t.start()
                _prompt_tasks[sid] = t
                continue

            # All other methods
            if method in ("mcp.install_server", "session.compress",
                          "session.file_revert", "session.file_revert_all"):
                # 长阻塞操作（下载安装 / 上下文压缩调 LLM / 文件回退复制与校验）
                # → 线程池执行，避免阻塞 WS 事件循环
                result = await asyncio.to_thread(handle_request, method, params, rid or "")
            else:
                result = handle_request(method, params, rid or "")
            if result is not None:
                await ws.send(json.dumps(result, ensure_ascii=False))
            elif rid:
                await ws.send(json.dumps({
                    "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }, ensure_ascii=False))

    except websockets.ConnectionClosed as e:
        logger.info(f"[WS] Connection closed: {remote} code={e.code}")
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
    finally:
        _connections.discard(ws)

async def main(host: str = "127.0.0.1", port: int = 19876):
    global _loop, _mcp_init_task, _gateway_token
    _loop = asyncio.get_running_loop()
    set_ws_loop(_loop)

    _gateway_token = _load_or_create_gateway_token()
    logger.info(f"[WS] Gateway token loaded from {os.path.join(_DATA_HOME, 'gateway.token')} (delete the file to rotate)")

    logger.info(f"[WS] Starting WebSocket gateway on ws://{host}:{port}")

    # 注册 TodoManager 的 WebSocket 推送回调
    async def _ws_push_todo(event_type: str, payload: dict):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": event_type, "payload": payload},
        }, ensure_ascii=False)
        disconnected = set()
        for ws in _connections:
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                disconnected.add(ws)
        _connections.difference_update(disconnected)
    
    try:
        from tools.todo_tool import TodoManager
        TodoManager.register_ws_push(_ws_push_todo)
        logger.info("[WS] TodoManager WS push callback registered")
    except Exception as e:
        logger.warning(f"[WS] Failed to register TodoManager push: {e}")

    async def _on_permission_request(message: dict):
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "permission.request", "payload": message},
        }, ensure_ascii=False)
        disconnected = set()
        for ws in _connections:
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                disconnected.add(ws)
        _connections.difference_update(disconnected)
        
    svc = get_permission_service()
    svc.register_push_callback(_on_permission_request)
    logger.info("[WS] Permission push callback registered")

    # MCP 状态变化推送：连接/断开完成后前端无需轮询
    async def _broadcast_mcp_state(state):
        payload = {
            "servers": [
                {
                    "name": c.name,
                    "state": getattr(c, "type", ""),
                    "error": getattr(c, "error", None),
                }
                for c in getattr(state, "clients", [])
            ]
        }
        msg = json.dumps({
            "jsonrpc": "2.0",
            "method": "event",
            "params": {"type": "mcp.state_changed", "payload": payload},
        }, ensure_ascii=False)
        disconnected = set()
        for ws in _connections:
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                disconnected.add(ws)
        _connections.difference_update(disconnected)

    def _on_mcp_state(state):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = _loop
        if loop is None:
            return
        try:
            loop.create_task(_broadcast_mcp_state(state))
        except RuntimeError:
            asyncio.run_coroutine_threadsafe(_broadcast_mcp_state(state), loop)

    try:
        from agent_core.mcp.mcp_manager import get_mcp_manager
        get_mcp_manager().on_state_change(_on_mcp_state)
        logger.info("[WS] MCP state push callback registered")
    except Exception as e:
        logger.warning(f"[WS] Failed to register MCP state push: {e}")

    # 后台启动 MCP 自动初始化
    _mcp_init_task = asyncio.create_task(_init_mcp_background())

    # 后台校验审计日志完整性（启动时一次）
    asyncio.create_task(_verify_audit_integrity_background())

    # max_size: allow large prompt.submit messages with base64 image attachments
    # (websockets default 1 MiB rejects them with close code 1009); the real
    # guard is _check_payload_limits plus the connection cap above.
    async with websockets.serve(handle_ws, host, port, ping_interval=30, ping_timeout=10, max_size=_MAX_MESSAGE_SIZE):
        await asyncio.Future()  # run forever

def run_server(host: str = "127.0.0.1", port: int = 19876):
    """Entry point: run the WebSocket server."""
    from gateway.log_setup import setup_logging
    setup_logging()
    asyncio.run(main(host, port))

if __name__ == "__main__":
    run_server()
