"""
MCP Client - MCP 核心客户端

实现 MCP 协议的核心客户端功能：
- 连接管理（stdio、HTTP 传输）
- 工具列表获取和调用
- 资源列表和读取
- 命令/Prompts 列表和执行
- 连接缓存和重连
- 错误处理和超时

MCP 协议：
- 基于 JSON-RPC 2.0
- stdio: 通过子进程 stdin/stdout 通信
- HTTP: 通过 POST 请求通信（支持 Streamable HTTP）
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Any, Tuple, Union

from .mcp_types import (
    ScopedMcpServerConfig,
    ConnectedMCPServer,
    FailedMCPServer,
    MCPServerConnection,
    ServerResource,
    SerializedMCPTool,
    MCPToolResult,
    get_config_type,
)

logger = logging.getLogger(__name__)

# ==================== HTTP 导入（可选） ====================

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    logger.warning("aiohttp not installed, HTTP transport will not be available")


# ==================== 常量 ====================

DEFAULT_MCP_TOOL_TIMEOUT_MS = 15_000   # 15 秒
DEFAULT_CONNECT_TIMEOUT_MS = 60_000     # 60 秒连接超时
DEFAULT_REQUEST_TIMEOUT_MS = 60_000     # 60 秒请求超时
MAX_MCP_DESCRIPTION_LENGTH = 2048       # 工具描述最大长度（防止超长描述）

# MCP 工具名称前缀
MCP_TOOL_PREFIX = "mcp__"


# ==================== 工具名称处理 ====================

def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """构建 MCP 工具完全限定名: mcp__server__tool"""
    normalized_server = server_name.replace("-", "_").lower()
    normalized_tool = tool_name.replace("-", "_").lower()
    return f"{MCP_TOOL_PREFIX}{normalized_server}__{normalized_tool}"


def parse_mcp_tool_name(full_name: str) -> Optional[Tuple[str, str]]:
    """解析 MCP 工具名: mcp__server__tool -> (server_name, tool_name)"""
    if not full_name.startswith(MCP_TOOL_PREFIX):
        return None
    
    rest = full_name[len(MCP_TOOL_PREFIX):]
    parts = rest.split("__", 1)
    if len(parts) != 2:
        return None
    
    return (parts[0], parts[1])


# ==================== 超时处理 ====================

class TimeoutError(Exception):
    """操作超时错误"""
    pass


async def with_timeout(coro, timeout_ms: int, error_message: str = "Operation timed out"):
    """带超时的协程执行"""
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
        return result
    except asyncio.TimeoutError:
        raise TimeoutError(error_message)


# ==================== stdio 传输 ====================

class StdioTransport:
    """
    Stdio 传输层
    
    通过子进程 stdin/stdout 进行 JSON-RPC 通信。
    每个消息是一行完整的 JSON，以 \n 分隔。
    
    Windows 兼容说明：使用 subprocess.Popen + 后台线程读取，
    而非 asyncio.create_subprocess_exec。后者在某些 Go 编译的
    Windows 二进制上会出现 stdout readline 永远挂起的问题。
    """
    
    def __init__(self, command: str, args: List[str], env: Optional[Dict[str, str]] = None):
        self.command = command
        self.args = args
        self.env = env
        
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._notification_handlers: Dict[str, List[callable]] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = asyncio.Lock()
        self._stdout_queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._dispatch_task: Optional[asyncio.Task] = None
    
    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None
    
    async def connect(self) -> None:
        """启动子进程并建立连接"""
        # 占位符路径检测：未配置的 server 直接报明确错误，不尝试启动
        if self.command and "REPLACE_WITH" in self.command:
            raise RuntimeError(
                f"MCP server command is a placeholder ('{self.command}'); "
                f"configure a real executable path to enable this server"
            )
        # 清理上一次连接的残留状态
        self._running = False
        self._pending_requests.clear()

        proc_env = os.environ.copy()
        if self.env:
            proc_env.update(self.env)
        
        exe_path, exe_args = self._resolve_executable()
        
        popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
        )
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

        self._process = subprocess.Popen(
            [exe_path, *exe_args],
            **popen_kwargs
        )
        
        self._loop = asyncio.get_running_loop()
        self._running = True
        
        # 后台线程：stdout → asyncio.Queue
        self._reader_thread = threading.Thread(
            target=self._read_stdout_thread, daemon=True
        )
        self._reader_thread.start()
        
        # 后台线程：stderr → print
        self._stderr_thread = threading.Thread(
            target=self._read_stderr_thread, daemon=True
        )
        self._stderr_thread.start()
        
        # 等待旧 dispatch 任务结束（避免新旧任务同时操作 _pending_requests）
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._dispatch_task), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError) as e:
                logger.debug(f"[MCP Stdio] Previous dispatch task did not stop cleanly: {e}")
            except Exception as e:
                logger.warning(f"[MCP Stdio] Failed to stop previous dispatch task: {e}")

        # 启动 asyncio 消息分发
        self._dispatch_task = asyncio.create_task(self._dispatch_messages())
        
        logger.info(f"[MCP Stdio] Process started (PID: {self._process.pid})")
    
    def _read_stdout_thread(self):
        """后台线程：从 subprocess stdout 逐行读取，放入 asyncio.Queue"""
        try:
            while self._running and self._process and self._process.stdout:
                line = self._process.stdout.readline()
                if not line:
                    break
                line_str = line.decode('utf-8', errors='replace').strip()
                if line_str:
                    try:
                        self._loop.call_soon_threadsafe(
                            self._stdout_queue.put_nowait, line_str
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"[MCP Stdio Reader] {e}")
        finally:
            self._loop.call_soon_threadsafe(
                self._stdout_queue.put_nowait, None  # Sentinel
            )
    
    def _read_stderr_thread(self):
        """后台线程：从 subprocess stderr 读取日志"""
        try:
            while self._running and self._process and self._process.stderr:
                line = self._process.stderr.readline()
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if text:
                    logger.debug(f"[MCP Stderr] {text}")
        except Exception as e:
            logger.debug(f"[MCP Stderr Error] {e}")
    
    async def _dispatch_messages(self):
        """主 asyncio 循环：从队列取出消息并分发"""
        while self._running:
            line_str = await self._stdout_queue.get()
            if line_str is None:  # Sentinel
                logger.info("[MCP Stdio] Process stdout closed")
                self._running = False
                break
            try:
                message = json.loads(line_str)
                self._handle_message(message)
            except json.JSONDecodeError:
                logger.warning(f"[MCP Stdio] Failed to parse JSON: {line_str[:200]}")
        
        # 清理：拒绝所有待处理请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(ConnectionError("Process connection closed"))
        self._pending_requests.clear()
    
    async def _write_stdin(self, data: bytes):
        """通过 executor 写入 stdin（避免阻塞事件循环）"""
        if self._process and self._process.stdin:
            await self._loop.run_in_executor(None, self._process.stdin.write, data)
            await self._loop.run_in_executor(None, self._process.stdin.flush)
    
    def _handle_message(self, message: Dict[str, Any]):
        """处理收到的 JSON-RPC 消息"""
        msg_id = message.get("id")
        method = message.get("method")
        
        if msg_id is not None and "result" in message:
            future = self._pending_requests.pop(msg_id, None)
            if future and not future.done():
                logger.info(f"[MCP Stdio] Resolving pending request id={msg_id}")
                future._loop.call_soon_threadsafe(future.set_result, message["result"])
            else:
                logger.warning(f"[MCP Stdio] Received response for unknown/future id={msg_id}")
        
        elif msg_id is not None and "error" in message:
            future = self._pending_requests.pop(msg_id, None)
            if future and not future.done():
                error = message["error"]
                exc = RuntimeError(f"MCP Error {error.get('code')}: {error.get('message')}")
                future._loop.call_soon_threadsafe(future.set_exception, exc)
        
        elif method:
            handlers = self._notification_handlers.get(method, [])
            for handler in handlers:
                try:
                    handler(message.get("params", {}))
                except Exception as e:
                    logger.error(f"[MCP Stdio] Notification handler error for {method}: {e}")
    
    def _resolve_executable(self) -> Tuple[str, List[str]]:
        """
        解析可执行文件路径
        
        在 Windows 上需要 .cmd/.exe 扩展名，且 shutil.which 有时不自动匹配 PATHEXT。
        """
        exe = self.command
        args = list(self.args) if self.args else []
        
        if sys.platform == 'win32':
            # Windows: 先按扩展名查找
            for test in (exe, exe + '.cmd', exe + '.bat', exe + '.exe'):
                resolved = shutil.which(test)
                if resolved:
                    logger.info(f"[MCP Stdio] Resolved '{exe}' -> '{resolved}'")
                    return resolved, args
            
            # 如果找不到，扫描常见 Node.js 安装路径
            for base in (r'C:\Program Files\nodejs', r'C:\Program Files (x86)\nodejs',
                         os.path.expandvars(r'%APPDATA%\npm'), os.path.expandvars(r'%LOCALAPPDATA%\npm')):
                for ext in ('.cmd', '.bat', '.exe'):
                    candidate = os.path.join(base, exe + ext)
                    if os.path.isfile(candidate):
                        logger.info(f"[MCP Stdio] Found '{exe}' at '{candidate}'")
                        return candidate, args
            
            raise FileNotFoundError(
                f"找不到可执行文件 '{exe}'。请确保已安装并加入 PATH。\n"
                f"尝试的扩展名: .cmd, .bat, .exe\n"
                f"尝试的路径: {os.environ.get('PATH', '')}"
            )
        else:
            # POSIX
            resolved = shutil.which(exe)
            if resolved:
                return resolved, args
            raise FileNotFoundError(f"找不到可执行文件 '{exe}'")
    
    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None, 
                          timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
                          cancel_event: Optional[asyncio.Event] = None) -> Any:
        """发送 JSON-RPC 请求并等待响应"""
        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        
        # 发送请求前检查子进程是否仍存活
        if self._process and self._process.poll() is not None:
            self._running = False
            if not future.done():
                future.set_exception(ConnectionError(
                    f"MCP subprocess exited with code {self._process.returncode}"))
            raise ConnectionError(f"MCP subprocess already exited (code {self._process.returncode})")

        # 发送请求（通过 executor 避免阻塞事件循环）
        request_line = json.dumps(request, ensure_ascii=False) + "\n"
        if self.is_running:
            try:
                await self._write_stdin(request_line.encode('utf-8'))
            except (AttributeError, BrokenPipeError, ConnectionError) as e:
                self._running = False
                if not future.done():
                    future.set_exception(ConnectionError(f"MCP transport write failed: {e}"))
                raise
        else:
            if not future.done():
                future.set_exception(ConnectionError("MCP subprocess is not running"))
            raise ConnectionError("MCP subprocess is not running")
        
        # 如果有关联的取消事件，注册监听
        cancel_watcher: Optional[asyncio.Task] = None
        if cancel_event:
            async def watch_cancel():
                await cancel_event.wait()
                if not future.done():
                    future.set_exception(asyncio.CancelledError("Request cancelled by user"))
            cancel_watcher = asyncio.create_task(watch_cancel())
        
        # 等待响应，同时监视子进程是否死亡
        try:
            async def _result_or_death():
                poll_interval = 0.5
                deadline = time.time() + timeout_ms / 1000
                while True:
                    waiter = asyncio.ensure_future(asyncio.wait_for(
                        asyncio.shield(future), timeout=min(poll_interval, max(0, deadline - time.time()))))
                    try:
                        return await waiter
                    except asyncio.TimeoutError:
                        if time.time() >= deadline:
                            raise
                        if self._process and self._process.poll() is not None:
                            self._running = False
                            raise ConnectionError(
                                f"MCP subprocess exited with code {self._process.returncode} during request")
            
            result = await _result_or_death()
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise
        except Exception:
            self._pending_requests.pop(request_id, None)
            raise
        finally:
            # 清理 cancel watcher
            if cancel_watcher and not cancel_watcher.done():
                cancel_watcher.cancel()
    
    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None):
        """发送 JSON-RPC 通知（不等待响应）"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        
        if self.is_running and self._loop:
            try:
                notification_line = json.dumps(notification, ensure_ascii=False) + "\n"
                asyncio.run_coroutine_threadsafe(
                    self._write_stdin(notification_line.encode('utf-8')),
                    self._loop,
                )
            except (AttributeError, BrokenPipeError, ConnectionError) as e:
                self._running = False
                logger.error(f"[MCP Stdio] Failed to send notification: {e}")
    
    def set_notification_handler(self, method: str, handler: callable):
        """注册通知处理器"""
        if method not in self._notification_handlers:
            self._notification_handlers[method] = []
        self._notification_handlers[method].append(handler)
    
    def remove_notification_handler(self, method: str):
        """移除通知处理器"""
        self._notification_handlers.pop(method, None)
    
    async def close(self):
        """关闭连接"""
        self._running = False
        
        # 等待 dispatch 任务结束
        if self._dispatch_task and not self._dispatch_task.done():
            try:
                self._dispatch_task.cancel()
                await asyncio.shield(self._dispatch_task)
            except Exception:
                pass

        # 终止进程
        if self._process:
            try:
                self._process.stdin.close()
                self._process.terminate()
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: self._process.wait(timeout=5)
                    )
                except Exception:
                    self._process.kill()
            except Exception as e:
                logger.error(f"[MCP Stdio] Error closing process: {e}")
        
        self._process = None


# ==================== HTTP 传输 ====================

class HttpTransport:
    """
    HTTP 传输层
    
    通过 HTTP POST 请求进行 JSON-RPC 通信。
    支持 Streamable HTTP 协议。
    """
    
    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None):
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self._request_id = 0
        self._session: Optional[aiohttp.ClientSession] = None
        self._notification_handlers: Dict[str, List[callable]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(self) -> None:
        """建立 HTTP 会话"""
        if not HAS_AIOHTTP:
            raise ImportError("aiohttp is required for HTTP transport")
        
        self._session = aiohttp.ClientSession(
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PebbleElectron-MCP/1.0",
                "Accept": "application/json, text/event-stream",
                **self.headers
            }
        )
        logger.info(f"[MCP HTTP] Session created for {self.url}")
    
    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None,
                          timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
                          cancel_event: Optional[asyncio.Event] = None) -> Any:
        """发送 JSON-RPC 请求
        
        Args:
            cancel_event: 可选的取消事件，设置后立即中断等待
        """
        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        if not self._session:
            raise ConnectionError("HTTP session not initialized")
        
        # 如果有取消事件，用 race 方式执行
        if cancel_event:
            async def do_request():
                async with self._session.post(
                    self.url,
                    json=request,
                    timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000)
                ) as response:
                    if response.status != 200:
                        error_body = await response.text()
                        raise RuntimeError(f"HTTP {response.status}: {error_body[:500]}")
                    return await response.json()
            
            async def wait_cancel():
                await cancel_event.wait()
                raise asyncio.CancelledError("Request cancelled by user")
            
            done, pending = await asyncio.wait(
                [asyncio.create_task(do_request()), asyncio.create_task(wait_cancel())],
                return_when=asyncio.FIRST_COMPLETED
            )
            # 取消未完成的任务
            for task in pending:
                task.cancel()
            # 返回完成的任务的结果（如果有异常会抛出）
            for task in done:
                result = task.result()
                if "error" in result:
                    error = result["error"]
                    raise RuntimeError(f"MCP Error {error.get('code')}: {error.get('message')}")
                return result.get("result")
        
        # 无取消事件：普通请求
        try:
            async with self._session.post(
                self.url,
                json=request,
                timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000)
            ) as response:
                if response.status != 200:
                    error_body = await response.text()
                    raise RuntimeError(f"HTTP {response.status}: {error_body[:500]}")
                
                result = await response.json()
                
                if "error" in result:
                    error = result["error"]
                    raise RuntimeError(f"MCP Error {error.get('code')}: {error.get('message')}")
                
                return result.get("result")
        except aiohttp.ClientError as e:
            raise ConnectionError(f"HTTP request failed: {e}")
    
    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None):
        """发送通知（fire-and-forget）"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        
        if self._session:
            asyncio.create_task(self._send_notification_async(notification))
    
    async def _send_notification_async(self, notification: Dict[str, Any]):
        """异步发送通知"""
        try:
            async with self._session.post(
                self.url,
                json=notification,
                timeout=aiohttp.ClientTimeout(total=10)
            ):
                pass  # 通知不关心响应
        except Exception as e:
            logger.debug(f"[MCP HTTP] Notification failed: {e}")
    
    def set_notification_handler(self, method: str, handler: callable):
        """注册通知处理器"""
        if method not in self._notification_handlers:
            self._notification_handlers[method] = []
        self._notification_handlers[method].append(handler)
    
    def remove_notification_handler(self, method: str):
        """移除通知处理器"""
        self._notification_handlers.pop(method, None)
    
    async def close(self):
        """关闭 HTTP 会话"""
        if self._session:
            await self._session.close()
            self._session = None


# ==================== 核心 MCP 客户端 ====================

class McpClient:
    """
    MCP 客户端
    
    封装 MCP 协议操作：
    - 连接管理
    - 工具列表/调用
    - 资源列表/读取
    - 命令/Prompts 列表/获取
    """
    
    def __init__(self, name: str, config: ScopedMcpServerConfig):
        self.name = name
        self.config = config
        self._transport: Optional[Union[StdioTransport, HttpTransport]] = None
        self._server_capabilities: Dict[str, Any] = {}
        self._server_info: Optional[Dict[str, str]] = None
        self._server_version: Optional[str] = None
        self._instructions: Optional[str] = None
        self._connected = False
        self._lock = asyncio.Lock()
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def transport(self):
        return self._transport
    
    async def connect(self) -> MCPServerConnection:
        """
        连接到 MCP 服务器
        
        Returns:
            MCPServerConnection - 连接结果
        """
        if self._connected:
            logger.info(f"[MCP] Already connected to '{self.name}'")
            return self._wrap_connection_result()
        
        config_type = get_config_type(self.config.config)
        connect_start = time.time()
        
        try:
            # 1. 创建传输层
            if config_type in (None, "stdio"):
                await self._connect_stdio()
            elif config_type == "http":
                await self._connect_http()
            elif config_type == "sse":
                # SSE 使用 HTTP 传输层（后续可优化为真正的 SSE）
                await self._connect_http()
            else:
                raise ValueError(f"Unsupported transport type: {config_type}")
            
            # 2. 初始化握手
            await self._initialize()
            
            # 3. 获取能力
            self._server_capabilities = await self._fetch_capabilities()
            
            elapsed = time.time() - connect_start
            logger.info(f"[MCP] Connected to '{self.name}' in {elapsed:.2f}s "
                       f"(transport: {config_type or 'stdio'})")
            
            self._connected = True
            return self._wrap_connection_result()
            
        except Exception as e:
            elapsed = time.time() - connect_start
            logger.error(f"[MCP] Failed to connect to '{self.name}' after {elapsed:.2f}s: {e}")
            
            await self._cleanup_transport()
            return FailedMCPServer(
                name=self.name,
                config=self.config,
                error=str(e)
            )
    
    async def _connect_stdio(self):
        """连接 stdio 传输"""
        stdio_config = self.config.config  # type: McpStdioServerConfig
        transport = StdioTransport(
            command=stdio_config.command,
            args=stdio_config.args or [],
            env=stdio_config.env
        )
        await transport.connect()
        self._transport = transport
    
    async def _connect_http(self):
        """连接 HTTP 传输"""
        if not HAS_AIOHTTP:
            raise ImportError("aiohttp is required for HTTP transport")
        
        http_config = self.config.config
        if not hasattr(http_config, 'url'):
            raise ValueError("HTTP config requires a URL")
        
        headers = getattr(http_config, 'headers', None)
        transport = HttpTransport(
            url=http_config.url,  # type: ignore
            headers=headers  # type: ignore
        )
        await transport.connect()
        self._transport = transport
    
    async def _initialize(self):
        """发送 initialize 请求"""
        result = await self._transport.send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {}
                },
                "clientInfo": {
                    "name": "PebbleElectron",
                    "version": "1.0.0"
                }
            },
            timeout_ms=DEFAULT_CONNECT_TIMEOUT_MS
        )
        
        if result:
            self._server_capabilities = result.get("capabilities", {})
            self._server_info = result.get("serverInfo")
            self._server_version = result.get("protocolVersion")
            self._instructions = result.get("instructions")
        
        # 发送 initialized 通知
        self._transport.send_notification("notifications/initialized")
    
    async def _fetch_capabilities(self) -> Dict[str, Any]:
        """获取服务器能力"""
        return self._server_capabilities
    
    async def get_server_instructions(self) -> Optional[str]:
        """获取服务器指令"""
        if not self._instructions:
            return None
        
        if len(self._instructions) > MAX_MCP_DESCRIPTION_LENGTH:
            return self._instructions[:MAX_MCP_DESCRIPTION_LENGTH] + "… [truncated]"
        return self._instructions
    
    async def list_tools(self) -> List[SerializedMCPTool]:
        """获取工具列表"""
        if not self._transport:
            logger.debug("[MCP] list_tools skipped: no transport")
            return []
        # 只要服务器连接成功，就尝试获取工具（不完全依赖 capabilities 声明）
        # 因为有些 MCP 服务器声明 "tools": {} 但实际有工具
        
        try:
            result = await self._transport.send_request("tools/list")
            tools = result.get("tools", [])
            
            serialized_tools = []
            for tool in tools:
                serialized = SerializedMCPTool(
                    name=build_mcp_tool_name(self.name, tool.get("name", "")),
                    description=tool.get("description", "") or "",
                    input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
                    original_tool_name=tool.get("name"),
                    server_name=self.name,
                    annotations=tool.get("annotations", {})
                )
                
                # 截断超长描述
                if len(serialized.description) > MAX_MCP_DESCRIPTION_LENGTH:
                    serialized.description = serialized.description[:MAX_MCP_DESCRIPTION_LENGTH] + "… [truncated]"
                
                serialized_tools.append(serialized)
            
            return serialized_tools
        except Exception as e:
            logger.error(f"[MCP] Failed to list tools for '{self.name}': {e}")
            return []
    
    async def call_tool(self, tool_name: str, args: Dict[str, Any],
                        timeout_ms: int = DEFAULT_MCP_TOOL_TIMEOUT_MS,
                        cancel_event: Optional[asyncio.Event] = None) -> MCPToolResult:
        """调用工具
        
        自动检测子进程是否已退出，如果已退出则自动重连并重试一次。
        
        Args:
            cancel_event: 可选的取消事件，设置后中断工具调用
        """
        if not self._transport or not self._connected:
            raise ConnectionError(f"MCP server '{self.name}' is not connected")
        
        # 调用前检查进程是否存活（进程可能已在多次调用后意外死亡）
        if hasattr(self._transport, '_process') and self._transport._process:
            if self._transport._process.poll() is not None:
                logger.warning(f"[MCP] Process for '{self.name}' died (exit code: {self._transport._process.returncode}), auto-reconnecting...")
                await self._cleanup_transport()
                self._connected = False
                result = await self.connect()
                if isinstance(result, FailedMCPServer):
                    return MCPToolResult(
                        content=[{"type": "text", "text": f"Error: MCP server '{self.name}' process died and reconnect failed: {result.error}"}],
                        is_error=True
                    )
        
        for attempt in range(2):
            try:
                result = await self._transport.send_request(
                    "tools/call",
                    {
                        "name": tool_name,
                        "arguments": args
                    },
                    timeout_ms=timeout_ms,
                    cancel_event=cancel_event
                )
                
                content = result.get("content", [])
                is_error = result.get("isError", False)
                _meta = result.get("_meta")
                
                return MCPToolResult(
                    content=content,
                    is_error=is_error,
                    _meta=_meta
                )
            except ConnectionError:
                if attempt == 0:
                    logger.warning(f"[MCP] Connection lost for '{self.name}', attempting reconnect...")
                    await self._cleanup_transport()
                    self._connected = False
                    result = await self.connect()
                    if isinstance(result, FailedMCPServer):
                        return MCPToolResult(
                            content=[{"type": "text", "text": f"Error calling tool '{tool_name}' on '{self.name}': reconnect failed: {result.error}"}],
                            is_error=True
                        )
                    continue
                return MCPToolResult(
                    content=[{"type": "text", "text": f"Error calling tool '{tool_name}' on '{self.name}': connection lost and reconnect failed"}],
                    is_error=True
                )
            except Exception as e:
                return MCPToolResult(
                    content=[{"type": "text", "text": f"Error calling tool '{tool_name}' on '{self.name}': {e}"}],
                    is_error=True
                )
    
    async def list_resources(self) -> List[ServerResource]:
        """获取资源列表"""
        if not self._transport or not self._server_capabilities.get("resources"):
            return []
        
        try:
            result = await self._transport.send_request("resources/list")
            resources = result.get("resources", [])
            
            return [
                ServerResource(
                    uri=r.get("uri", ""),
                    name=r.get("name", ""),
                    server=self.name,
                    description=r.get("description"),
                    mime_type=r.get("mimeType"),
                    size=r.get("size")
                )
                for r in resources
            ]
        except Exception as e:
            logger.error(f"[MCP] Failed to list resources for '{self.name}': {e}")
            return []
    
    async def read_resource(self, uri: str) -> List[Dict[str, Any]]:
        """读取资源"""
        if not self._transport:
            raise ConnectionError(f"MCP server '{self.name}' is not connected")
        
        try:
            result = await self._transport.send_request(
                "resources/read",
                {"uri": uri}
            )
            return result.get("contents", [])
        except Exception as e:
            logger.error(f"[MCP] Failed to read resource '{uri}' from '{self.name}': {e}")
            return [{"uri": uri, "text": f"Error reading resource: {e}"}]
    
    async def list_prompts(self) -> List[Dict[str, Any]]:
        """获取 Prompts 列表"""
        if not self._transport or not self._server_capabilities.get("prompts"):
            return []
        
        try:
            result = await self._transport.send_request("prompts/list")
            return result.get("prompts", [])
        except Exception as e:
            logger.error(f"[MCP] Failed to list prompts for '{self.name}': {e}")
            return []
    
    async def get_prompt(self, name: str, args: Dict[str, str] = None) -> List[Dict[str, Any]]:
        """获取 Prompt"""
        if not self._transport:
            raise ConnectionError(f"MCP server '{self.name}' is not connected")
        
        try:
            result = await self._transport.send_request(
                "prompts/get",
                {"name": name, "arguments": args or {}}
            )
            return result.get("messages", [])
        except Exception as e:
            logger.error(f"[MCP] Failed to get prompt '{name}' from '{self.name}': {e}")
            return []
    
    def _wrap_connection_result(self) -> MCPServerConnection:
        """包装连接结果"""
        if self._connected:
            return ConnectedMCPServer(
                name=self.name,
                config=self.config,
                capabilities=self._server_capabilities,
                server_info=self._server_info,
                instructions=self._instructions,
                server_version=self._server_version,
                _client=self,
                _transport=self._transport
            )
        else:
            return FailedMCPServer(
                name=self.name,
                config=self.config,
                error="Not connected"
            )
    
    async def _cleanup_transport(self):
        """清理传输层"""
        if self._transport:
            try:
                await self._transport.close()
            except Exception as e:
                logger.error(f"[MCP] Error closing transport for '{self.name}': {e}")
            self._transport = None
    
    async def close(self):
        """关闭客户端连接"""
        self._connected = False
        await self._cleanup_transport()


# ==================== 连接缓存 ====================

# 全局连接缓存（{cache_key: MCPServerConnection}）
_connection_cache: Dict[str, Tuple[ConnectedMCPServer, McpClient]] = {}
_cache_lock = asyncio.Lock()


def _get_cache_key(name: str, config: ScopedMcpServerConfig) -> str:
    """生成缓存键"""
    config_type = get_config_type(config.config)
    if config_type in (None, "stdio") and hasattr(config.config, 'command'):
        return f"{name}-{getattr(config.config, 'command')}"
    elif hasattr(config.config, 'url'):
        return f"{name}-{getattr(config.config, 'url')}"  # type: ignore
    return f"{name}-{config_type}"


async def connect_to_server(name: str, config: ScopedMcpServerConfig) -> MCPServerConnection:
    """连接到 MCP 服务器（带缓存）"""
    cache_key = _get_cache_key(name, config)
    
    async with _cache_lock:
        if cache_key in _connection_cache:
            existing_server, existing_client = _connection_cache[cache_key]
            # 不仅检查 _connected flag，还要验证底层进程是否真的活着
            transport_alive = False
            if existing_client._transport and hasattr(existing_client._transport, '_process'):
                proc = existing_client._transport._process
                transport_alive = proc is not None and proc.poll() is None
            elif existing_client._transport and hasattr(existing_client._transport, '_session'):
                transport_alive = existing_client._transport._session is not None
            
            if existing_client.is_connected and transport_alive:
                logger.debug(f"[MCP] Using cached connection for '{name}'")
                return existing_server
            else:
                logger.warning(f"[MCP] Cached connection for '{name}' is dead (connected={existing_client.is_connected}, transport_alive={transport_alive}), reconnecting...")
                await existing_client.close()
                del _connection_cache[cache_key]
        
        # 创建新连接
        client = McpClient(name, config)
        result = await client.connect()
        
        if result.type == "connected" and isinstance(result, ConnectedMCPServer):
            _connection_cache[cache_key] = (result, client)
        
        return result


async def ensure_connected_client(client: ConnectedMCPServer) -> ConnectedMCPServer:
    """确保客户端已连接，如果断开则重连"""
    if isinstance(client, ConnectedMCPServer) and client._client and client._client.is_connected:
        return client
    
    logger.info(f"[MCP] Reconnecting to '{client.name}'")
    result = await connect_to_server(client.name, client.config)
    
    if result.type == "connected" and isinstance(result, ConnectedMCPServer):
        return result
    
    raise ConnectionError(f"MCP server '{client.name}' is not connected")


async def clear_server_cache(name: str, config: Optional[ScopedMcpServerConfig] = None):
    """清除服务器缓存"""
    async with _cache_lock:
        keys_to_delete = []
        for cache_key, (server, client) in _connection_cache.items():
            if server.name == name:
                keys_to_delete.append(cache_key)
                await client.close()
        
        for key in keys_to_delete:
            del _connection_cache[key]


# ==================== 批量操作 ====================

async def fetch_tools_for_client(server: ConnectedMCPServer) -> List[SerializedMCPTool]:
    """获取已连接服务器的工具"""
    if not isinstance(server, ConnectedMCPServer) or not server._client:
        return []
    return await server._client.list_tools()


async def fetch_resources_for_client(server: ConnectedMCPServer) -> List[ServerResource]:
    """获取已连接服务器的资源"""
    if not isinstance(server, ConnectedMCPServer) or not server._client:
        return []
    return await server._client.list_resources()


async def fetch_commands_for_client(server: ConnectedMCPServer) -> List[Dict[str, Any]]:
    """获取已连接服务器的命令（Prompts）"""
    if not isinstance(server, ConnectedMCPServer) or not server._client:
        return []
    return await server._client.list_prompts()


async def call_mcp_tool(server: ConnectedMCPServer, tool_name: str, 
                        args: Dict[str, Any],
                        cancel_event: Optional[asyncio.Event] = None) -> MCPToolResult:
    """调用 MCP 工具
    
    Args:
        cancel_event: 可选的取消事件
    """
    if not isinstance(server, ConnectedMCPServer) or not server._client:
        raise ConnectionError(f"MCP server '{server.name}' is not connected")
    return await server._client.call_tool(tool_name, args, cancel_event=cancel_event)


async def get_mcp_tools_commands_and_resources(
    configs: Dict[str, ScopedMcpServerConfig],
    on_connection_attempt=None
) -> Tuple[List[MCPServerConnection], List[SerializedMCPTool], List[Dict[str, Any]], Dict[str, List[ServerResource]]]:
    """
    批量获取 MCP 工具、命令和资源
    """
    clients: List[MCPServerConnection] = []
    all_tools: List[SerializedMCPTool] = []
    all_commands: List[Dict[str, Any]] = []
    all_resources: Dict[str, List[ServerResource]] = {}
    
    # 并发连接
    tasks = []
    for name, config in configs.items():
        tasks.append((name, config, asyncio.create_task(connect_to_server(name, config))))
    
    for name, config, task in tasks:
        try:
            client = await task
            clients.append(client)
            
            if client.type == "connected" and isinstance(client, ConnectedMCPServer):
                # 获取工具
                tools = await fetch_tools_for_client(client)
                all_tools.extend(tools)
                
                # 获取命令
                commands = await fetch_commands_for_client(client)
                all_commands.extend(commands)
                
                # 获取资源
                resources = await fetch_resources_for_client(client)
                if resources:
                    all_resources[name] = resources
            
            if on_connection_attempt:
                on_connection_attempt(client, 
                    [t for t in all_tools if t.server_name == name],
                    [c for c in all_commands],
                    all_resources.get(name, []))
                    
        except Exception as e:
            logger.error(f"[MCP] Failed to connect to '{name}': {e}")
            failed_client = FailedMCPServer(name=name, config=config, error=str(e))
            clients.append(failed_client)
            
            if on_connection_attempt:
                on_connection_attempt(failed_client, [], [], [])
    
    return clients, all_tools, all_commands, all_resources
