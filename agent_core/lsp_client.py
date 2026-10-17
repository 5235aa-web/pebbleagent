"""
LSP Client — JSON-RPC 2.0 over stdio 的完整 Language Server Protocol 客户端
- 子进程管理（自动发现 pyright / jedi-language-server / pylsp）
- JSON-RPC 2.0 协议（Content-Length header + JSON body）
- 请求/响应匹配（基于 id 的 Future 机制）
- 服务器通知处理（window/showMessage、textDocument/publishDiagnostics 等）
- 文件同步（textDocument/didOpen、didChange、didClose）
- 9 种 LSP 操作完整实现
- 10MB 文件大小限制
- URI ↔ 本地路径双向转换
- 自动重连（指数退避）
- 并发安全（asyncio.Lock 保护写操作）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, List, Optional,
)
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)

# ============================================================================
# 常量
# ============================================================================

# LSP 协议版本
LSP_PROTOCOL_VERSION = "2.0"

# 最大文件大小（10MB），超过此大小的文件不发送给 LSP
MAX_LSP_FILE_SIZE_BYTES = 10_000_000

# 请求超时
DEFAULT_REQUEST_TIMEOUT = 15.0  # 秒
INITIALIZE_TIMEOUT = 30.0  # 初始化超时更长

# 重连配置
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_BASE_DELAY = 1.0  # 秒
RECONNECT_MAX_DELAY = 30.0  # 秒

# 支持的 LSP 服务器（按优先级排序）
LSP_SERVER_CANDIDATES: List[Dict[str, Any]] = [
    {
        "name": "pyright",
        "display_name": "Pyright (Microsoft)",
        "type": "node",
        "command": "pyright-langserver",
        "args": ["--stdio"],
        "env": {},
        "language_ids": ["python"],
        "extensions": [".py", ".pyi", ".pyx"],
        "check_command": [],  # pyright-langserver 不支持 --version，通过 shutil.which 检测
    },
    {
        "name": "jedi-language-server",
        "display_name": "Jedi Language Server",
        "type": "python",
        "command": sys.executable,
        "args": ["-m", "jedi_language_server"],
        "env": {},
        "language_ids": ["python"],
        "extensions": [".py", ".pyi"],
        "check_command": [sys.executable, "-m", "jedi_language_server", "--version"],
    },
    {
        "name": "pylsp",
        "display_name": "Python LSP Server",
        "type": "python",
        "command": "pylsp",
        "args": [],
        "env": {},
        "language_ids": ["python"],
        "extensions": [".py", ".pyi"],
        "check_command": ["pylsp", "--version"],
    },
]


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class LspConnectionState:
    """LSP 连接状态"""
    connected: bool = False
    initialized: bool = False
    server_name: str = ""
    server_display_name: str = ""
    server_version: str = ""
    server_capabilities: Dict[str, Any] = field(default_factory=dict)
    root_uri: str = ""
    error: Optional[str] = None
    start_time: float = 0.0
    reconnect_attempts: int = 0
    last_error_time: float = 0.0


@dataclass
class LspLocation:
    """LSP 位置信息（从 LSP Location 转换而来）"""
    uri: str
    file_path: str           # 本地路径
    line: int                # 1-based
    character: int           # 1-based
    range_start_line: int    # 0-based LSP range
    range_start_character: int  # 0-based
    range_end_line: int      # 0-based
    range_end_character: int  # 0-based


@dataclass
class LspSymbolInfo:
    """LSP 符号信息"""
    name: str
    kind: int
    kind_name: str
    file_path: str
    line: int                # 1-based
    character: int           # 1-based
    container_name: Optional[str] = None
    detail: Optional[str] = None
    uri: str = ""


@dataclass
class LspCallHierarchyItem:
    """调用层次项"""
    name: str
    kind: int
    kind_name: str
    uri: str
    file_path: str
    line: int                # 1-based
    character: int           # 1-based
    detail: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LspCallHierarchyCall:
    """调用层次调用关系"""
    from_item: Optional[LspCallHierarchyItem] = None
    to_item: Optional[LspCallHierarchyItem] = None
    from_ranges: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 符号类型映射（LSP SymbolKind → 中文名称）
# ============================================================================

SYMBOL_KIND_NAMES: Dict[int, str] = {
    1: "文件(File)",
    2: "模块(Module)",
    3: "命名空间(Namespace)",
    4: "包(Package)",
    5: "类(Class)",
    6: "方法(Method)",
    7: "属性(Property)",
    8: "字段(Field)",
    9: "构造函数(Constructor)",
    10: "枚举(Enum)",
    11: "接口(Interface)",
    12: "函数(Function)",
    13: "变量(Variable)",
    14: "常量(Constant)",
    15: "字符串(String)",
    16: "数字(Number)",
    17: "布尔(Boolean)",
    18: "数组(Array)",
    19: "对象(Object)",
    20: "键(Key)",
    21: "空值(Null)",
    22: "枚举成员(EnumMember)",
    23: "结构体(Struct)",
    24: "事件(Event)",
    25: "运算符(Operator)",
    26: "类型参数(TypeParameter)",
}


def symbol_kind_name(kind: int) -> str:
    """获取符号类型的可读名称"""
    return SYMBOL_KIND_NAMES.get(kind, f"未知({kind})")


# ============================================================================
# URI ↔ 路径转换
# ============================================================================

def path_to_uri(file_path: str) -> str:
    """
    将本地文件路径转换为 file:// URI
    
    遵循 LSP 规范：
    - Windows: file:///C%3A/Users/... 
    - Linux/Mac: file:///home/user/...
    
    Args:
        file_path: 绝对文件路径
    
    Returns:
        file:// URI
    """
    abs_path = os.path.abspath(file_path)
    
    if os.name == 'nt':
        # Windows: C:\Users\... → file:///C%3A/Users/...
        # 将反斜杠转为正斜杠
        unix_path = abs_path.replace('\\', '/')
        # 对路径各部分进行编码，但保留斜杠
        parts = unix_path.split('/')
        encoded_parts = [quote(part, safe='') for part in parts]
        encoded_path = '/'.join(encoded_parts)
        return f"file:///{encoded_path}"
    else:
        # Unix: /home/user/... → file:///home/user/...
        parts = abs_path.split('/')
        encoded_parts = [quote(part, safe='') for part in parts]
        encoded_path = '/'.join(encoded_parts)
        return f"file://{encoded_path}"


def uri_to_path(uri: str) -> str:
    """
    将 file:// URI 转换为本地文件路径
    
    Args:
        uri: file:// URI
    
    Returns:
        本地文件路径
    """
    if not uri:
        return ""
    
    # 移除 file:// 前缀
    file_path = re.sub(r'^file://', '', uri)
    
    # Windows: file:///C%3A/... → /C:/... → C:/...
    if os.name == 'nt' and re.match(r'^/[A-Za-z]:', file_path):
        file_path = file_path[1:]
    
    # URL解码
    try:
        file_path = unquote(file_path)
    except Exception:
        pass  # 解码失败则使用原始路径
    
    # 规范化路径分隔符
    file_path = os.path.normpath(file_path)
    
    return file_path


def uri_to_relative_path(uri: str, cwd: str) -> str:
    """
    将 file:// URI 转换为相对于 cwd 的路径
    
    Args:
        uri: file:// URI
        cwd: 当前工作目录
    
    Returns:
        相对路径（如果不在 cwd 下则返回绝对路径）
    """
    file_path = uri_to_path(uri)
    if not file_path:
        return "<未知位置>"
    
    try:
        rel_path = os.path.relpath(file_path, cwd)
        # 规范化分隔符
        rel_path = rel_path.replace('\\', '/')
        # 如果相对路径太长（说明不在项目内），使用绝对路径
        if rel_path.startswith('../../'):
            return file_path.replace('\\', '/')
        return rel_path
    except ValueError:
        # 不同驱动器（Windows），使用绝对路径
        return file_path.replace('\\', '/')


# ============================================================================
# JSON-RPC 消息构建和解析
# ============================================================================

class JsonRpcError(Exception):
    """JSON-RPC 错误"""
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"JSON-RPC Error {code}: {message}")


def build_jsonrpc_request(method: str, params: Optional[Dict[str, Any]] = None, 
                          request_id: Optional[int] = None) -> bytes:
    """
    构建 JSON-RPC 2.0 请求消息
    
    Args:
        method: LSP 方法名
        params: 参数
        request_id: 请求 ID（None = 通知，不需要响应）
    
    Returns:
        完整的 LSP 消息字节（Content-Length header + body）
    """
    message: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    
    if params is not None:
        message["params"] = params
    
    if request_id is not None:
        message["id"] = request_id
    
    body = json.dumps(message, ensure_ascii=False)
    body_bytes = body.encode('utf-8')
    
    header = f"Content-Length: {len(body_bytes)}\r\n\r\n"
    return header.encode('utf-8') + body_bytes


def build_jsonrpc_notification(method: str, params: Optional[Dict[str, Any]] = None) -> bytes:
    """构建 JSON-RPC 通知（无 id，不需要响应）"""
    return build_jsonrpc_request(method, params, request_id=None)


def parse_jsonrpc_message(data: bytes) -> Dict[str, Any]:
    """解析 JSON-RPC 消息"""
    return json.loads(data.decode('utf-8'))


# ============================================================================
# LSP 消息读取器
# ============================================================================

class LspMessageReader:
    """
    LSP 消息读取器
    
    从子进程的 stdout 流中读取完整的 LSP 消息。
    LSP 消息格式：
        Content-Length: <bytes>\r\n
        Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n
        \r\n
        <json body>
    """
    
    def __init__(self, stream: asyncio.StreamReader):
        self._stream = stream
        self._buffer = b""
    
    async def read_message(self) -> Optional[Dict[str, Any]]:
        """
        从流中读取一条完整的 LSP 消息
        
        Returns:
            解析后的消息字典，或 None（流关闭）
        """
        # 读取 headers
        headers = {}
        while True:
            line = await self._stream.readline()
            if not line:
                return None  # 流关闭
            
            line = line.rstrip(b'\r\n')
            if not line:
                break  # 空行，headers 结束
            
            try:
                line_str = line.decode('utf-8')
                if ':' in line_str:
                    key, value = line_str.split(':', 1)
                    headers[key.strip().lower()] = value.strip()
            except UnicodeDecodeError:
                logger.warning("[LspReader] Failed to decode header line")
        
        # 获取 Content-Length
        content_length_str = headers.get('content-length', '0')
        try:
            content_length = int(content_length_str)
        except ValueError:
            logger.error(f"[LspReader] Invalid Content-Length: {content_length_str}")
            return None
        
        if content_length <= 0:
            logger.warning(f"[LspReader] Zero or negative Content-Length: {content_length}")
            return None
        
        # 读取 body
        body = await self._stream.readexactly(content_length)
        
        # 解析 JSON
        try:
            message = json.loads(body.decode('utf-8'))
            return message
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"[LspReader] Failed to parse message body: {e}")
            return None


# ============================================================================
# LSP 服务器发现
# ============================================================================

def find_available_lsp_server() -> Optional[Dict[str, Any]]:
    """
    查找系统中可用的 LSP 服务器
    
    按优先级依次检查每个候选服务器，返回第一个可用的。
    
    Returns:
        服务器配置字典，或 None（无一可用）
    """
    for candidate in LSP_SERVER_CANDIDATES:
        command_name = candidate["command"]
        server_type = candidate["type"]
        display_name = candidate["display_name"]
        
        if server_type == "node":
            # Pyright：通过 shutil.which 检测
            if shutil.which(command_name):
                logger.info(f"[LspDiscovery] Found LSP server: {display_name} at {shutil.which(command_name)}")
                return candidate
            # npx 备选方案
            if shutil.which("npx"):
                logger.info(f"[LspDiscovery] Found 'npx', will use it for {display_name}")
                npx_candidate = dict(candidate)
                npx_candidate["command"] = "npx"
                npx_candidate["args"] = ["-y", "pyright-langserver", "--stdio"]
                return npx_candidate
        
        elif server_type == "python":
            if shutil.which(command_name):
                logger.info(f"[LspDiscovery] Found LSP server: {display_name} at {shutil.which(command_name)}")
                return candidate
            
            # 通过 python -m 方式检查模块是否可导入
            module_name = candidate["name"].replace('-', '_')
            try:
                result = subprocess.run(
                    [sys.executable, "-c", f"import {module_name}"],
                    capture_output=True, timeout=10
                )
                if result.returncode == 0:
                    logger.info(f"[LspDiscovery] Found Python module: {candidate['name']}")
                    return candidate
            except Exception:
                pass
    
    logger.warning("[LspDiscovery] No LSP server found. Install one of: pyright, jedi-language-server, python-lsp-server")
    return None


# ============================================================================
# LSP 客户端核心
# ============================================================================

class LspClient:
    """
    Language Server Protocol 客户端
    
    通过 stdio 与 LSP 服务器通信，实现异步 JSON-RPC 2.0 协议。
    
    使用方式:
        client = LspClient()
        await client.initialize(project_path="/path/to/project")
        
        # 打开文件后即可请求
        await client.open_file("/path/to/file.py", content)
        result = await client.go_to_definition("/path/to/file.py", line=10, character=5)
        
        await client.shutdown()
    
    线程安全：写操作由 _write_lock 保护，读操作由 _reader_task 独占。
    """
    
    _instance: Optional["LspClient"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._state = LspConnectionState()
        
        # 子进程
        self._process: Optional[asyncio.subprocess.Process] = None
        
        # I/O
        self._reader_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()
        
        # 请求匹配
        self._request_id: int = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        
        # 文件追踪
        self._open_files: Dict[str, str] = {}  # uri → content
        
        # 服务器配置
        self._server_config: Optional[Dict[str, Any]] = None
        
        # 取消信号
        self._shutdown_event = asyncio.Event()
        
        # 通知处理器
        self._notification_handlers: Dict[str, List[Callable]] = {}
        
        # 诊断收集（key: 标准化后的文件路径, value: 诊断列表）
        self._diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        # 诊断推送事件（key: 标准化路径, value: asyncio.Event 用于等待）
        self._diagnostics_events: Dict[str, asyncio.Event] = {}
        
        logger.info("[LspClient] Instance created")
    
    # ========================================================================
    # 状态查询
    # ========================================================================
    
    @property
    def is_connected(self) -> bool:
        return self._state.connected
    
    @property
    def is_initialized(self) -> bool:
        return self._state.initialized
    
    def get_state(self) -> LspConnectionState:
        return self._state
    
    def is_file_open(self, file_path: str) -> bool:
        """检查文件是否已在 LSP 服务器中打开"""
        uri = path_to_uri(file_path)
        return uri in self._open_files
    
    # ========================================================================
    # 连接管理
    # ========================================================================
    
    async def initialize(self, project_path: str) -> bool:
        """
        初始化 LSP 客户端：发现服务器 → 启动进程 → 握手
        
        Args:
            project_path: 项目根目录
        
        Returns:
            是否成功初始化
        """
        if self._state.initialized:
            logger.info("[LspClient] Already initialized")
            return True
        
        # 1. 发现服务器
        server_config = find_available_lsp_server()
        if not server_config:
            self._state.error = "No LSP server found. Install pyright or jedi-language-server."
            logger.error(f"[LspClient] {self._state.error}")
            return False
        
        self._server_config = server_config
        self._state.server_name = server_config["name"]
        self._state.server_display_name = server_config["display_name"]
        self._state.start_time = time.time()
        
        logger.info(f"[LspClient] Using LSP server: {server_config['display_name']}")
        
        # 2. 启动子进程
        success = await self._start_process()
        if not success:
            return False
        
        # 3. 发送 initialize 请求
        success = await self._send_initialize(os.path.abspath(project_path))
        if not success:
            return False
        
        # 4. 发送 initialized 通知
        await self._send_notification("initialized", {})
        
        self._state.initialized = True
        self._state.connected = True
        
        logger.info(f"[LspClient] Initialized successfully with {server_config['display_name']}")
        return True
    
    async def _start_process(self) -> bool:
        """启动 LSP 服务器子进程"""
        if not self._server_config:
            return False
        
        config = self._server_config
        command = config["command"]
        args = list(config.get("args", []))  # 复制，不修改原始配置
        
        logger.info(f"[LspClient] Starting process: {command} {' '.join(args)}")
        
        try:
            # Windows 上处理 .CMD 文件：需要使用 cmd /c 包装
            resolved_command = shutil.which(command)
            if resolved_command and os.name == 'nt' and resolved_command.upper().endswith('.CMD'):
                # 将 cmd /c 作为命令，原始 command args 作为参数
                actual_cmd = os.environ.get('COMSPEC', 'cmd.exe')
                full_args = ['/c', resolved_command] + args
                logger.info(f"[LspClient] Windows .CMD detected, using: {actual_cmd} {' '.join(full_args)}")
            elif resolved_command:
                actual_cmd = resolved_command
                full_args = args
            else:
                actual_cmd = command
                full_args = args
            
            # 设置环境变量
            env = os.environ.copy()
            env.update(config.get("env", {}))
            
            self._process = await asyncio.create_subprocess_exec(
                actual_cmd,
                *full_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            
            # 启动消息读取任务
            self._reader_task = asyncio.create_task(self._read_messages())
            
            # 启动 stderr 日志任务
            asyncio.create_task(self._log_stderr())
            
            self._state.connected = True  # 进程已启动，可以发送/接收消息
            
            logger.info(f"[LspClient] Process started, PID={self._process.pid}")
            return True
            
        except FileNotFoundError:
            self._state.error = f"LSP server executable not found: {command}"
            logger.error(f"[LspClient] {self._state.error}")
            return False
        except Exception as e:
            self._state.error = f"Failed to start LSP server: {e}"
            logger.error(f"[LspClient] {self._state.error}")
            return False
    
    async def _send_initialize(self, root_path: str) -> bool:
        """
        发送 initialize 请求并处理响应
        
        LSP 初始化握手：
        1. 客户端发送 initialize 请求
        2. 服务器返回 InitializeResult（包含 capabilities）
        3. 客户端发送 initialized 通知
        
        Args:
            root_path: 项目根目录的绝对路径
        
        Returns:
            是否成功
        """
        root_uri = path_to_uri(root_path)
        self._state.root_uri = root_uri
        
        params = {
            "processId": os.getpid(),
            "rootPath": root_path,
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": True,
                        "didSave": True,
                    },
                    "definition": {
                        "dynamicRegistration": True,
                        "linkSupport": True,
                    },
                    "references": {
                        "dynamicRegistration": True,
                    },
                    "hover": {
                        "dynamicRegistration": True,
                        "contentFormat": ["markdown", "plaintext"],
                    },
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {
                            "valueSet": list(range(1, 27))  # 所有 SymbolKind
                        },
                    },
                    "implementation": {
                        "dynamicRegistration": True,
                        "linkSupport": True,
                    },
                    "callHierarchy": {
                        "dynamicRegistration": True,
                    },
                },
                "workspace": {
                    "symbol": {
                        "dynamicRegistration": True,
                        "symbolKind": {
                            "valueSet": list(range(1, 27))
                        },
                    },
                },
            },
            "trace": "off",
            "workspaceFolders": [
                {
                    "uri": root_uri,
                    "name": os.path.basename(root_path) or "project",
                }
            ],
        }
        
        try:
            result = await self._send_request(
                "initialize", params, timeout=INITIALIZE_TIMEOUT
            )
            
            if result is None:
                self._state.error = "Initialize request returned no response"
                return False
            
            # 提取服务器能力
            capabilities = result.get("capabilities", {})
            self._state.server_capabilities = capabilities
            
            # 提取服务器信息
            server_info = result.get("serverInfo", {})
            self._state.server_version = server_info.get("version", "unknown")
            
            logger.info(f"[LspClient] Server capabilities: {list(capabilities.keys())}")
            logger.info(f"[LspClient] Server version: {self._state.server_version}")
            
            return True
            
        except asyncio.TimeoutError:
            self._state.error = "Initialize request timed out"
            logger.error(f"[LspClient] {self._state.error}")
            return False
        except JsonRpcError as e:
            self._state.error = f"Initialize request failed: {e}"
            logger.error(f"[LspClient] {self._state.error}")
            return False
        except Exception as e:
            self._state.error = f"Unexpected error during initialize: {e}"
            logger.error(f"[LspClient] {self._state.error}")
            return False
    
    async def shutdown(self) -> None:
        """关闭 LSP 客户端"""
        if not self._state.connected:
            return
        
        logger.info("[LspClient] Shutting down...")
        self._shutdown_event.set()
        
        try:
            # 发送 shutdown 请求
            await self._send_request("shutdown", {}, timeout=5.0)
        except Exception as e:
            logger.debug(f"[LspClient] shutdown request failed: {e}")
        
        try:
            # 发送 exit 通知
            await self._send_notification("exit", {})
        except Exception as e:
            logger.debug(f"[LspClient] exit notification failed: {e}")
        
        # 取消读取任务
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        
        # 终止进程
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                logger.debug(f"[LspClient] process termination failed: {e}")
        
        # 取消所有待处理的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(ConnectionError("LSP client shut down"))
        self._pending_requests.clear()
        
        # 清理诊断数据
        self._diagnostics.clear()
        for event in self._diagnostics_events.values():
            event.set()
        self._diagnostics_events.clear()
        
        self._state.connected = False
        self._state.initialized = False
        self._open_files.clear()
        
        logger.info("[LspClient] Shut down complete")
    
    async def reconnect(self) -> bool:
        """
        重新连接 LSP 服务器（指数退避）
        
        Returns:
            是否重连成功
        """
        if self._state.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"[LspClient] Max reconnect attempts ({MAX_RECONNECT_ATTEMPTS}) reached")
            return False
        
        # 先关闭现有连接
        await self.shutdown()
        self._shutdown_event.clear()
        
        # 计算延迟
        delay = min(
            RECONNECT_BASE_DELAY * (2 ** self._state.reconnect_attempts),
            RECONNECT_MAX_DELAY
        )
        self._state.reconnect_attempts += 1
        
        logger.info(f"[LspClient] Reconnecting in {delay:.1f}s (attempt {self._state.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})")
        await asyncio.sleep(delay)
        
        # 重新初始化
        root_path = uri_to_path(self._state.root_uri) if self._state.root_uri else os.getcwd()
        success = await self.initialize(root_path)
        
        if success:
            self._state.reconnect_attempts = 0
            logger.info("[LspClient] Reconnected successfully")
        else:
            logger.error(f"[LspClient] Reconnect attempt {self._state.reconnect_attempts} failed")
        
        return success
    
    # ========================================================================
    # JSON-RPC 通信
    # ========================================================================
    
    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, 
                            timeout: float = DEFAULT_REQUEST_TIMEOUT) -> Any:
        """
        发送 JSON-RPC 请求并等待响应
        
        Args:
            method: LSP 方法名
            params: 参数
            timeout: 超时秒数
        
        Returns:
            响应的 result 字段
        
        Raises:
            asyncio.TimeoutError: 请求超时
            JsonRpcError: 服务器返回错误
            ConnectionError: 未连接
        """
        if not self._state.connected:
            raise ConnectionError("LSP client not connected")
        
        # 分配请求 ID
        self._request_id += 1
        request_id = self._request_id
        
        # 创建 Future 用于等待响应
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future
        
        try:
            # 发送请求
            message = build_jsonrpc_request(method, params, request_id)
            
            async with self._write_lock:
                if self._process and self._process.stdin:
                    self._process.stdin.write(message)
                    await self._process.stdin.drain()
            
            # 等待响应（带超时）
            response = await asyncio.wait_for(future, timeout=timeout)
            
            # 检查是否有错误
            if isinstance(response, dict) and "error" in response:
                error = response["error"]
                raise JsonRpcError(
                    code=error.get("code", -1),
                    message=error.get("message", "Unknown error"),
                    data=error.get("data")
                )
            
            # 返回 result
            if isinstance(response, dict):
                return response.get("result")
            return response
            
        finally:
            self._pending_requests.pop(request_id, None)
    
    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """
        发送 JSON-RPC 通知（无需等待响应）
        
        Args:
            method: LSP 方法名
            params: 参数
        """
        if not self._state.connected:
            return
        
        message = build_jsonrpc_notification(method, params)
        
        async with self._write_lock:
            if self._process and self._process.stdin:
                self._process.stdin.write(message)
                await self._process.stdin.drain()
    
    async def _read_messages(self) -> None:
        """
        后台任务：持续从服务器 stdout 读取消息并分发
        
        处理两种消息：
        - 响应（有 id 字段）→ 完成对应的 Future
        - 通知（有 method 字段，无 id）→ 调用通知处理器
        """
        if not self._process or not self._process.stdout:
            return
        
        reader = LspMessageReader(self._process.stdout)
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        reader.read_message(), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if message is None:
                    # 流关闭
                    logger.warning("[LspClient] Server stdout closed unexpectedly")
                    self._state.connected = False
                    self._state.error = "Server stdout closed"
                    
                    # 取消所有待处理的请求
                    for future in self._pending_requests.values():
                        if not future.done():
                            future.set_exception(ConnectionError("LSP server disconnected"))
                    self._pending_requests.clear()
                    break
                
                # 分发消息
                if "id" in message:
                    # 响应消息
                    request_id = message["id"]
                    future = self._pending_requests.get(request_id)
                    if future and not future.done():
                        future.set_result(message)
                elif "method" in message:
                    # 通知消息
                    method = message["method"]
                    params = message.get("params", {})
                    await self._handle_notification(method, params)
                else:
                    logger.warning(f"[LspClient] Unknown message format: {list(message.keys())}")
                    
        except asyncio.CancelledError:
            logger.info("[LspClient] Message reader cancelled")
        except Exception as e:
            logger.error(f"[LspClient] Message reader error: {e}")
            self._state.error = f"Message reader error: {e}"
    
    async def _handle_notification(self, method: str, params: Dict[str, Any]) -> None:
        """
        处理服务器通知
        
        Args:
            method: 通知方法名
            params: 通知参数
        """
        # 调用注册的处理器
        handlers = self._notification_handlers.get(method, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(params)
                else:
                    handler(params)
            except Exception as e:
                logger.warning(f"[LspClient] Notification handler error for {method}: {e}")
        
        # 内置处理
        if method == "window/showMessage":
            msg_type = params.get("type", 0)  # 1=Error, 2=Warning, 3=Info, 4=Log
            msg_text = params.get("message", "")
            if msg_type == 1:
                logger.error(f"[LspServer] {msg_text}")
            elif msg_type == 2:
                logger.warning(f"[LspServer] {msg_text}")
            else:
                logger.info(f"[LspServer] {msg_text}")
        
        elif method == "window/logMessage":
            logger.debug(f"[LspServer Log] {params.get('message', '')}")
        
        elif method == "textDocument/publishDiagnostics":
            diagnostics = params.get("diagnostics", [])
            uri = params.get("uri", "unknown")
            file_path = uri_to_path(uri)
            normalized = os.path.normpath(file_path) if file_path else uri
            
            # 存储诊断
            self._diagnostics[normalized] = diagnostics
            
            # 通知等待者
            event = self._diagnostics_events.get(normalized)
            if event:
                event.set()
            
            errors = sum(1 for d in diagnostics if d.get("severity") == 1)
            warnings = sum(1 for d in diagnostics if d.get("severity") == 2)
            if errors or warnings:
                logger.debug(
                    f"[LspClient] Diagnostics for {file_path}: "
                    f"{errors} errors, {warnings} warnings"
                )
    
    async def _log_stderr(self) -> None:
        """后台任务：记录服务器 stderr 输出"""
        if not self._process or not self._process.stderr:
            return
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    line = await asyncio.wait_for(
                        self._process.stderr.readline(), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                if not line:
                    break
                
                line_str = line.decode('utf-8', errors='replace').rstrip()
                if line_str:
                    logger.debug(f"[LspServer stderr] {line_str}")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[LspClient] stderr reader error: {e}")
    
    # ========================================================================
    # 文件同步
    # ========================================================================
    
    async def open_file(self, file_path: str, content: str) -> bool:
        """
        在 LSP 服务器中打开文件
        
        LSP 要求先发送 textDocument/didOpen 才能对该文件进行查询操作。
        自动检查文件大小限制。
        
        Args:
            file_path: 文件绝对路径
            content: 文件内容
        
        Returns:
            是否成功打开
        """
        if not self._state.initialized:
            logger.warning("[LspClient] Cannot open file: client not initialized")
            return False
        
        uri = path_to_uri(file_path)
        
        # 检查是否已打开
        if uri in self._open_files:
            # 更新内容
            self._open_files[uri] = content
            await self._send_notification("textDocument/didChange", {
                "textDocument": {
                    "uri": uri,
                    "version": int(time.time()),
                },
                "contentChanges": [
                    {"text": content}
                ],
            })
            return True
        
        # 检查文件大小
        file_size = len(content.encode('utf-8'))
        if file_size > MAX_LSP_FILE_SIZE_BYTES:
            logger.warning(
                f"[LspClient] File too large: {file_path} "
                f"({file_size / 1_000_000:.1f}MB > {MAX_LSP_FILE_SIZE_BYTES / 1_000_000:.0f}MB)"
            )
            return False
        
        # 检测语言 ID
        ext = os.path.splitext(file_path)[1].lower()
        language_id = self._get_language_id(ext)
        
        # 发送 didOpen 通知
        await self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": int(time.time()),
                "text": content,
            },
        })
        
        self._open_files[uri] = content
        
        # 等待服务器索引（给服务器一些时间处理）
        await asyncio.sleep(0.3)
        
        logger.debug(f"[LspClient] Opened file: {file_path} ({file_size} bytes, language={language_id})")
        return True
    
    async def close_file(self, file_path: str) -> None:
        """关闭 LSP 服务器中的文件"""
        uri = path_to_uri(file_path)
        
        if uri in self._open_files:
            await self._send_notification("textDocument/didClose", {
                "textDocument": {"uri": uri},
            })
            del self._open_files[uri]
            logger.debug(f"[LspClient] Closed file: {file_path}")
    
    def _get_language_id(self, ext: str) -> str:
        """根据文件扩展名获取 LSP 语言标识符"""
        language_map = {
            ".py": "python",
            ".pyi": "python",
            ".pyx": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".jsx": "javascriptreact",
            ".json": "json",
            ".html": "html",
            ".css": "css",
            ".md": "markdown",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".sql": "sql",
            ".sh": "shellscript",
            ".bash": "shellscript",
        }
        return language_map.get(ext, "plaintext")
    
    async def ensure_file_open(self, file_path: str) -> bool:
        """
        确保文件已在 LSP 服务器中打开
        
        如果未打开，自动读取文件内容并发送 didOpen。
        如果文件太大（>10MB），返回 False。
        
        Args:
            file_path: 文件路径
        
        Returns:
            文件是否已打开或成功打开
        """
        if self.is_file_open(file_path):
            return True
        
        # 检查文件是否存在
        if not os.path.isfile(file_path):
            logger.warning(f"[LspClient] File not found: {file_path}")
            return False
        
        # 读取文件内容
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"[LspClient] Failed to read file {file_path}: {e}")
            return False
        
        return await self.open_file(file_path, content)
    
    # ========================================================================
    # LSP 操作 — 9 种方法
    # ========================================================================
    
    async def _ensure_connected(self) -> None:
        """确保客户端已连接，未连接则尝试初始化"""
        if not self._state.initialized:
            raise ConnectionError("LSP client not initialized. Ensure lsp_client.initialize() was called.")
        if not self._state.connected:
            raise ConnectionError("LSP client disconnected. Call reconnect() to restore.")
    
    def _make_position(self, line: int, character: int) -> Dict[str, int]:
        """
        将 1-based 行号/列号转换为 0-based LSP position
        
        Args:
            line: 1-based 行号（用户友好）
            character: 1-based 列号（用户友好）
        
        Returns:
            0-based LSP position
        """
        return {
            "line": max(0, line - 1),
            "character": max(0, character - 1),
        }
    
    # === 1. goToDefinition ===
    
    async def go_to_definition(
        self, file_path: str, line: int, character: int
    ) -> List[LspLocation]:
        """
        跳转到定义
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            定义位置列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        uri = path_to_uri(file_path)
        position = self._make_position(line, character)
        
        result = await self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": position,
        })
        
        return self._parse_locations(result)
    
    # === 2. findReferences ===
    
    async def find_references(
        self, file_path: str, line: int, character: int
    ) -> List[LspLocation]:
        """
        查找所有引用
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            引用位置列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        uri = path_to_uri(file_path)
        position = self._make_position(line, character)
        
        result = await self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": position,
            "context": {"includeDeclaration": True},
        })
        
        return self._parse_locations(result)
    
    # === 3. hover ===
    
    async def hover(
        self, file_path: str, line: int, character: int
    ) -> Optional[str]:
        """
        获取悬停信息
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            悬停信息文本，或 None
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return None
        
        uri = path_to_uri(file_path)
        position = self._make_position(line, character)
        
        result = await self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": position,
        })
        
        if not result:
            return None
        
        return self._extract_hover_text(result)
    
    def _extract_hover_text(self, hover_result: Dict[str, Any]) -> str:
        """
        从 Hover 结果中提取文本
        
        支持 MarkupContent 和 MarkedString 两种格式
        """
        contents = hover_result.get("contents")
        if not contents:
            return ""
        
        # MarkedString 或 MarkedString[]
        if isinstance(contents, str):
            return contents
        if isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("value", ""))
            return "\n\n".join(parts)
        
        # MarkupContent: { kind: "markdown"|"plaintext", value: "..." }
        if isinstance(contents, dict):
            return contents.get("value", "")
        
        return str(contents)
    
    # === 4. documentSymbol ===
    
    async def document_symbol(self, file_path: str) -> List[LspSymbolInfo]:
        """
        获取文档符号（文件大纲）
        
        支持两种 LSP 返回格式：
        - DocumentSymbol[]（层次结构，有 range 和 children）
        - SymbolInformation[]（扁平列表，有 location）
        
        Args:
            file_path: 文件路径
        
        Returns:
            符号信息列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        uri = path_to_uri(file_path)
        
        result = await self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        
        if not result or not isinstance(result, list):
            return []
        
        if len(result) == 0:
            return []
        
        # 检测格式：DocumentSymbol 有 'range'，SymbolInformation 有 'location'
        if 'range' in result[0]:
            # DocumentSymbol[] — 递归解析
            symbols = []
            for ds in result:
                symbols.extend(self._flatten_document_symbols(ds, uri))
            return symbols
        else:
            # SymbolInformation[]
            return self._parse_symbol_informations(result)
    
    def _flatten_document_symbols(
        self, symbol: Dict[str, Any], uri: str, depth: int = 0
    ) -> List[LspSymbolInfo]:
        """递归展开 DocumentSymbol（可能包含 children）"""
        symbols = []
        
        range_info = symbol.get("range", {})
        start = range_info.get("start", {})
        
        symbols.append(LspSymbolInfo(
            name=symbol.get("name", ""),
            kind=symbol.get("kind", 0),
            kind_name=symbol_kind_name(symbol.get("kind", 0)),
            file_path=uri_to_path(uri),
            line=start.get("line", 0) + 1,
            character=start.get("character", 0) + 1,
            detail=symbol.get("detail"),
            uri=uri,
        ))
        
        # 递归处理子符号
        children = symbol.get("children", [])
        for child in children:
            symbols.extend(self._flatten_document_symbols(child, uri, depth + 1))
        
        return symbols
    
    def _parse_symbol_informations(self, symbols: List[Dict[str, Any]]) -> List[LspSymbolInfo]:
        """解析 SymbolInformation[]"""
        result = []
        for sym in symbols:
            location = sym.get("location", {})
            range_info = location.get("range", {})
            start = range_info.get("start", {})
            
            result.append(LspSymbolInfo(
                name=sym.get("name", ""),
                kind=sym.get("kind", 0),
                kind_name=symbol_kind_name(sym.get("kind", 0)),
                file_path=uri_to_path(location.get("uri", "")),
                line=start.get("line", 0) + 1,
                character=start.get("character", 0) + 1,
                container_name=sym.get("containerName"),
                uri=location.get("uri", ""),
            ))
        
        return result
    
    # === 5. workspaceSymbol ===
    
    async def workspace_symbol(self, query: str = "") -> List[LspSymbolInfo]:
        """
        搜索工作区符号
        
        Args:
            query: 搜索查询（空字符串返回所有符号）
        
        Returns:
            符号信息列表
        """
        await self._ensure_connected()
        
        result = await self._send_request("workspace/symbol", {
            "query": query or "",
        })
        
        if not result or not isinstance(result, list):
            return []
        
        return self._parse_symbol_informations(result)
    
    # === 6. goToImplementation ===
    
    async def go_to_implementation(
        self, file_path: str, line: int, character: int
    ) -> List[LspLocation]:
        """
        跳转到实现
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            实现位置列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        uri = path_to_uri(file_path)
        position = self._make_position(line, character)
        
        result = await self._send_request("textDocument/implementation", {
            "textDocument": {"uri": uri},
            "position": position,
        })
        
        return self._parse_locations(result)
    
    # === 7. prepareCallHierarchy ===
    
    async def prepare_call_hierarchy(
        self, file_path: str, line: int, character: int
    ) -> List[LspCallHierarchyItem]:
        """
        准备调用层次
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            调用层次项列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        uri = path_to_uri(file_path)
        position = self._make_position(line, character)
        
        result = await self._send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri},
            "position": position,
        })
        
        if not result or not isinstance(result, list):
            return []
        
        return self._parse_call_hierarchy_items(result)
    
    def _parse_call_hierarchy_items(self, items: List[Dict[str, Any]]) -> List[LspCallHierarchyItem]:
        """解析 CallHierarchyItem[]"""
        result = []
        for item in items:
            range_info = item.get("range", {})
            start = range_info.get("start", {})
            
            result.append(LspCallHierarchyItem(
                name=item.get("name", ""),
                kind=item.get("kind", 0),
                kind_name=symbol_kind_name(item.get("kind", 0)),
                uri=item.get("uri", ""),
                file_path=uri_to_path(item.get("uri", "")),
                line=start.get("line", 0) + 1,
                character=start.get("character", 0) + 1,
                detail=item.get("detail"),
                raw=item,
            ))
        
        return result
    
    # === 8. incomingCalls ===
    
    async def incoming_calls(
        self, file_path: str, line: int, character: int
    ) -> List[LspCallHierarchyCall]:
        """
        获取传入调用（谁调用了这个函数）
        
        注意：这是两步操作：
        1. prepareCallHierarchy 获取 CallHierarchyItem
        2. callHierarchy/incomingCalls 获取实际调用者
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            传入调用列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        # 第一步：prepareCallHierarchy
        items = await self.prepare_call_hierarchy(file_path, line, character)
        if not items:
            return []
        
        # 第二步：对第一个 item 请求 incomingCalls
        result = await self._send_request("callHierarchy/incomingCalls", {
            "item": items[0].raw,
        })
        
        if not result or not isinstance(result, list):
            return []
        
        return self._parse_call_hierarchy_calls(result, direction="incoming")
    
    # === 9. outgoingCalls ===
    
    async def outgoing_calls(
        self, file_path: str, line: int, character: int
    ) -> List[LspCallHierarchyCall]:
        """
        获取传出调用（这个函数调用了谁）
        
        Args:
            file_path: 文件路径
            line: 1-based 行号
            character: 1-based 列号
        
        Returns:
            传出调用列表
        """
        await self._ensure_connected()
        
        if not await self.ensure_file_open(file_path):
            return []
        
        # 第一步：prepareCallHierarchy
        items = await self.prepare_call_hierarchy(file_path, line, character)
        if not items:
            return []
        
        # 第二步：对第一个 item 请求 outgoingCalls
        result = await self._send_request("callHierarchy/outgoingCalls", {
            "item": items[0].raw,
        })
        
        if not result or not isinstance(result, list):
            return []
        
        return self._parse_call_hierarchy_calls(result, direction="outgoing")
    
    def _parse_call_hierarchy_calls(
        self, calls: List[Dict[str, Any]], direction: str
    ) -> List[LspCallHierarchyCall]:
        """解析 CallHierarchyIncomingCall[] 或 CallHierarchyOutgoingCall[]"""
        result = []
        for call in calls:
            from_item = None
            to_item = None
            
            if direction == "incoming":
                # incomingCalls: { from: CallHierarchyItem, fromRanges: Range[] }
                from_data = call.get("from")
                if from_data:
                    from_item = self._parse_single_call_hierarchy_item(from_data)
            else:
                # outgoingCalls: { to: CallHierarchyItem, fromRanges: Range[] }
                to_data = call.get("to")
                if to_data:
                    to_item = self._parse_single_call_hierarchy_item(to_data)
            
            result.append(LspCallHierarchyCall(
                from_item=from_item,
                to_item=to_item,
                from_ranges=call.get("fromRanges", []),
                raw=call,
            ))
        
        return result
    
    def _parse_single_call_hierarchy_item(self, item: Dict[str, Any]) -> LspCallHierarchyItem:
        """解析单个 CallHierarchyItem"""
        range_info = item.get("range", {})
        start = range_info.get("start", {})
        
        return LspCallHierarchyItem(
            name=item.get("name", ""),
            kind=item.get("kind", 0),
            kind_name=symbol_kind_name(item.get("kind", 0)),
            uri=item.get("uri", ""),
            file_path=uri_to_path(item.get("uri", "")),
            line=start.get("line", 0) + 1,
            character=start.get("character", 0) + 1,
            detail=item.get("detail"),
            raw=item,
        )
    
    # ========================================================================
    # 诊断收集与文件刷新
    # ========================================================================
    
    async def touch_file(self, file_path: str) -> bool:
        """
        通知 LSP 服务器文件内容已变更，触发重新诊断。
        
        如果文件已在 LSP 中打开，发送 didChange 通知。
        否则读取文件并发送 didOpen 通知。
        
        Args:
            file_path: 文件绝对路径
        
        Returns:
            是否成功
        """
        if not self._state.initialized:
            return False
        
        uri = path_to_uri(file_path)
        normalized = os.path.normpath(file_path)
        
        # 清除旧诊断，为新诊断准备事件
        self._diagnostics.pop(normalized, None)
        self._diagnostics_events[normalized] = asyncio.Event()
        
        if uri in self._open_files:
            # 文件已打开 → 发送 didChange 通知
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._open_files[uri] = content
            except Exception:
                pass
            
            await self._send_notification("textDocument/didChange", {
                "textDocument": {
                    "uri": uri,
                    "version": int(time.time()),
                },
                "contentChanges": [
                    {"text": self._open_files.get(uri, "")}
                ],
            })
            return True
        
        # 文件未打开 → 发送 didOpen
        return await self.ensure_file_open(file_path)
    
    def get_diagnostics(self, file_path: str) -> List[Dict[str, Any]]:
        """
        获取文件的 LSP 诊断结果（非阻塞，返回已缓存的诊断）。
        
        Args:
            file_path: 文件路径
        
        Returns:
            诊断列表，按 severity 排序（ERROR 优先）
        """
        normalized = os.path.normpath(file_path)
        diags = self._diagnostics.get(normalized, [])
        # 按 severity 排序: 1(ERROR) 优先
        return sorted(diags, key=lambda d: d.get("severity", 4))
    
    async def wait_for_diagnostics(
        self, file_path: str, timeout: float = 3.0
    ) -> List[Dict[str, Any]]:
        """
        等待 LSP 服务器推送诊断结果。
        
        Args:
            file_path: 文件路径
            timeout: 等待超时（秒）
        
        Returns:
            诊断列表
        """
        normalized = os.path.normpath(file_path)
        event = self._diagnostics_events.get(normalized)
        
        if event is None:
            # touch_file 未被调用，直接返回缓存
            return self.get_diagnostics(file_path)
        
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug(f"[LspClient] Wait for diagnostics timed out: {file_path}")
        finally:
            self._diagnostics_events.pop(normalized, None)
        
        return self.get_diagnostics(file_path)
    
    @staticmethod
    def report_diagnostics(file_path: str, diagnostics: List[Dict[str, Any]],
                           max_per_file: int = 20) -> str:
        """
        格式化诊断结果为人类可读的报告。
        
        仅报告 severity=1（ERROR）的诊断，忽略 WARNING/INFO/HINT。
        
        Args:
            file_path: 文件路径
            diagnostics: 诊断列表
            max_per_file: 每个文件最多报告的错误数
        
        Returns:
            格式化的诊断报告字符串，无错误时返回空字符串
        """
        severity_names = {1: "ERROR", 2: "WARN", 3: "INFO", 4: "HINT"}
        errors = [d for d in diagnostics if d.get("severity") == 1]
        
        if not errors:
            return ""
        
        limited = errors[:max_per_file]
        more = len(errors) - max_per_file
        
        lines = [f'<diagnostics file="{file_path}">']
        for d in limited:
            sev = severity_names.get(d.get("severity", 1), "UNKNOWN")
            line = d.get("range", {}).get("start", {}).get("line", 0) + 1
            col = d.get("range", {}).get("start", {}).get("character", 0) + 1
            msg = d.get("message", "")
            lines.append(f"{sev} [{line}:{col}] {msg}")
        
        if more > 0:
            lines.append(f"... 还有 {more} 个错误")
        
        lines.append("</diagnostics>")
        return "\n".join(lines)
    
    # ========================================================================
    # 辅助方法
    # ========================================================================
    
    def _parse_locations(self, result: Any) -> List[LspLocation]:
        """
        解析 LSP Location 或 LocationLink 结果
        
        支持格式：
        - Location: { uri, range: { start: {line, character}, end: {line, character} } }
        - LocationLink: { targetUri, targetRange, targetSelectionRange }
        - Location[] 或 LocationLink[]
        - 单个 Location 或 LocationLink
        - null（返回空列表）
        """
        if not result:
            return []
        
        # 统一为列表
        if not isinstance(result, list):
            result = [result]
        
        locations = []
        for item in result:
            if not isinstance(item, dict):
                continue
            
            # 判断是 LocationLink 还是 Location
            if "targetUri" in item:
                # LocationLink
                uri = item.get("targetUri", "")
                range_info = item.get("targetSelectionRange", item.get("targetRange", {}))
            else:
                # Location
                uri = item.get("uri", "")
                range_info = item.get("range", {})
            
            if not uri:
                continue
            
            start = range_info.get("start", {})
            end = range_info.get("end", {})
            
            locations.append(LspLocation(
                uri=uri,
                file_path=uri_to_path(uri),
                line=start.get("line", 0) + 1,
                character=start.get("character", 0) + 1,
                range_start_line=start.get("line", 0),
                range_start_character=start.get("character", 0),
                range_end_line=end.get("line", 0),
                range_end_character=end.get("character", 0),
            ))
        
        return locations
    
    def count_unique_files(self, locations: List[LspLocation]) -> int:
        """统计位置列表中唯一的文件数"""
        return len(set(loc.uri for loc in locations if loc.uri))


# ============================================================================
# 全局实例
# ============================================================================

def get_lsp_client() -> LspClient:
    """获取全局 LspClient 单例"""
    return LspClient()


async def ensure_lsp_initialized(project_path: str) -> Optional[LspClient]:
    """
    确保 LSP 客户端已初始化
    
    如果尚未初始化，自动发现服务器并初始化。
    
    Args:
        project_path: 项目根目录
    
    Returns:
        LspClient 实例，或 None（初始化失败）
    """
    client = get_lsp_client()
    
    if client.is_initialized:
        return client
    
    success = await client.initialize(project_path)
    if not success:
        logger.warning(f"[LspClient] Failed to initialize for project: {project_path}")
        return None
    
    return client
