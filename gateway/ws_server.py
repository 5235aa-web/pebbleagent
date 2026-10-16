"""
PebbleAgent GUI Gateway - WebSocket JSON-RPC 2.0 server.

Replaces the stdin/stdout TUI gateway with a WebSocket server
suitable for the GUI frontend.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from typing import Any, Optional

import websockets
from websockets.server import WebSocketServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gateway.server import handle_request, handle_prompt_submit, set_ws_loop
from gateway.permission_service import get_permission_service

logger = logging.getLogger("ws-gateway")

# ===== Project root (same as gateway.server) =====
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ===== Active connections =====
_connections: set[websockets.WebSocketServerProtocol] = set()

# ===== Running prompt tasks =====
_prompt_tasks: dict[str, threading.Thread] = {}

class WebSocketWriter:
    """Wraps a websocket connection to provide write_event() compatible with handle_prompt_submit."""

    def __init__(self, ws: websockets.WebSocketServerProtocol):
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

async def handle_ws(ws: websockets.WebSocketServerProtocol):
    """Handle a single WebSocket connection."""
    global _loop
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
            if method in ("mcp.install_server", "session.compress"):
                # 长阻塞操作（下载安装 / 上下文压缩调 LLM，可能数十秒）→ 线程池执行，避免阻塞 WS 事件循环
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
    global _loop, _mcp_init_task
    _loop = asyncio.get_running_loop()
    set_ws_loop(_loop)

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

    # 后台启动 MCP 自动初始化
    _mcp_init_task = asyncio.create_task(_init_mcp_background())

    # max_size=None: disable the per-message size limit (websockets default 1 MiB
    # rejects prompt.submit with large image attachments as base64, close code 1009)
    async with websockets.serve(handle_ws, host, port, ping_interval=30, ping_timeout=10, max_size=None):
        await asyncio.Future()  # run forever

def run_server(host: str = "127.0.0.1", port: int = 19876):
    """Entry point: run the WebSocket server."""
    from gateway.log_setup import setup_logging
    setup_logging()
    asyncio.run(main(host, port))

if __name__ == "__main__":
    run_server()
