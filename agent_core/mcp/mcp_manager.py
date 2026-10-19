"""
MCP Manager - MCP 连接管理器

管理 MCP 服务器连接的生命周期：
- 连接初始化（批量并发）
- 状态管理（connected/failed/pending/disabled）
- 自动重连（指数退避）
- ListChanged 通知处理
- 启用/禁用管理
- 工具/命令/资源状态同步

- useManageMCPConnections.ts → McpManager
- MCPConnectionManager.tsx → 管理状态变更
- onConnectionAttempt → _update_server_state
"""

import asyncio
import logging
import os
import shutil
import sys
from typing import Dict, List, Optional, Any, Callable

from .mcp_types import (
    MCPServerConnection,
    ConnectedMCPServer,
    FailedMCPServer,
    PendingMCPServer,
    DisabledMCPServer,
    ScopedMcpServerConfig,
    SerializedMCPTool,
    SerializedMCPClient,
    ServerResource,
    MCPCliState,
    is_local_mcp_server,
)
from .mcp_config import (
    get_mcp_config_manager,
    is_mcp_server_disabled,
    set_mcp_server_enabled,
)
from .mcp_client import (
    connect_to_server,
    clear_server_cache,
    fetch_tools_for_client,
    fetch_resources_for_client,
    fetch_commands_for_client,
)

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

MAX_RECONNECT_ATTEMPTS = 5        # 最大重连次数
INITIAL_BACKOFF_MS = 1000         # 初始退避时间
MAX_BACKOFF_MS = 30000            # 最大退避时间
MCP_BATCH_FLUSH_MS = 0.016        # 批量刷新间隔（16ms）
MAX_CONCURRENT_LOCAL = 3          # 本地服务器最大并发连接
MAX_CONCURRENT_REMOTE = 20        # 远程服务器最大并发连接


def _check_command_dependency(config) -> Optional[str]:
    """
    依赖预检：检查服务器的 command 是否可用。

    返回缺失描述（字符串）或 None（可用/不适用）。
    对未安装依赖（Node.js / uv 等）的服务器直接给出明确状态，
    避免无谓的进程启动尝试（供应链安全 + 启动体验）。
    """
    # ScopedMcpServerConfig 为包装类，真实配置在 .config 字段
    inner = getattr(config, "config", None)
    command = getattr(inner, "command", None)
    if command is None:
        command = getattr(config, "command", None)
    if not command:
        return None  # 远程（HTTP/SSE）服务器无本地 command

    if os.path.isabs(command):
        return None if os.path.isfile(command) else f"command not found: {command}"

    candidates = [command]
    if sys.platform == "win32":
        candidates += [command + ".cmd", command + ".bat", command + ".exe"]
    for c in candidates:
        if shutil.which(c):
            return None

    if command == "npx":
        return "requires Node.js (npx not found in PATH)"
    if command == "uvx":
        return "requires uv (uvx not found in PATH)"
    return f"command not found in PATH: {command}"


# ==================== MCP 管理器 ====================

class McpManager:
    """
    MCP 连接管理器
    
    管理所有 MCP 服务器连接的生命周期。
    单例模式，全局共享状态。
    """
    
    def __init__(self):
        self._initialized = False
        self._initializing = False
        
        # 连接状态
        self._clients: Dict[str, MCPServerConnection] = {}
        self._configs: Dict[str, ScopedMcpServerConfig] = {}
        
        # 工具/命令/资源状态
        self._tools: List[SerializedMCPTool] = []
        self._commands: List[Dict[str, Any]] = []
        self._resources: Dict[str, List[ServerResource]] = {}
        
        # 规范化名称映射
        self._normalized_names: Dict[str, str] = {}
        
        # 重连状态
        self._reconnect_timers: Dict[str, asyncio.Task] = {}
        self._canceled = False
        
        # 回调
        self._on_state_change: List[Callable] = []
        self._on_connection_attempt: Optional[Callable] = None
        
        # 锁
        self._lock = asyncio.Lock()
        # 每服务器一把 toggle 锁，避免并发点击导致重复连接/断开
        self._toggle_locks: Dict[str, asyncio.Lock] = {}
        
        # 通知处理器（用于 ListChanged）
        self._notification_tasks: Dict[str, asyncio.Task] = {}
    
    # ==================== 初始化 ====================
    
    async def initialize(self, dynamic_configs: Dict[str, ScopedMcpServerConfig] = None):
        """
        初始化 MCP 管理器
        
        加载配置并连接所有启用的服务器。
        """
        if self._initialized or self._initializing:
            return
        
        self._initializing = True
        self._canceled = False
        
        try:
            logger.info("[MCP Manager] Initializing...")
            
            # 加载配置
            config_manager = get_mcp_config_manager()
            if dynamic_configs:
                config_manager.set_dynamic_configs(dynamic_configs)
            
            self._configs = config_manager.get_enabled_configs()
            
            if not self._configs:
                logger.info("[MCP Manager] No MCP servers configured")
                self._initialized = True
                return
            
            # 分批连接
            await self._connect_all()
            
            self._initialized = True
            logger.info(f"[MCP Manager] Initialized with "
                       f"{len(self._clients)} clients, {len(self._tools)} tools")
            
        except Exception as e:
            logger.error(f"[MCP Manager] Initialization failed: {e}")
            raise
        finally:
            self._initializing = False
    
    async def _connect_all(self):
        """连接所有配置的服务器"""
        # 分类服务器
        local_servers = {}
        remote_servers = {}
        
        for name, config in self._configs.items():
            if is_mcp_server_disabled(name):
                self._clients[name] = DisabledMCPServer(name=name, config=config)
                self._notify_state_change()
                continue

            # 依赖预检：command 缺失时直接标记失败（明确原因，不做无谓的启动尝试）
            dep_error = _check_command_dependency(config)
            if dep_error:
                logger.info(f"[MCP] Server '{name}' skipped: {dep_error}")
                self._clients[name] = FailedMCPServer(name=name, config=config, error=dep_error)
                self._notify_state_change()
                continue

            if is_local_mcp_server(config):
                local_servers[name] = config
            else:
                remote_servers[name] = config
        
        # 使用信号量控制并发
        local_semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOCAL)
        remote_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REMOTE)
        
        async def connect_with_semaphore(name, config):
            if is_local_mcp_server(config):
                async with local_semaphore:
                    await self._connect_and_process(name, config)
            else:
                async with remote_semaphore:
                    await self._connect_and_process(name, config)
        
        # 启动所有任务
        sem_tasks = []
        for name, config in local_servers.items():
            sem_tasks.append(asyncio.create_task(connect_with_semaphore(name, config)))
        for name, config in remote_servers.items():
            sem_tasks.append(asyncio.create_task(connect_with_semaphore(name, config)))
        
        await asyncio.gather(*sem_tasks, return_exceptions=True)
    
    async def _connect_and_process(self, name: str, config: ScopedMcpServerConfig):
        """连接单个服务器并处理结果"""
        try:
            # 添加到 pending 状态
            self._clients[name] = PendingMCPServer(name=name, config=config)
            self._notify_state_change()
            
            # 连接
            client = await connect_to_server(name, config)
            self._clients[name] = client
            
            if client.type == "connected" and isinstance(client, ConnectedMCPServer):
                await self._process_connected_client(client)
            
            self._notify_state_change()
            
        except asyncio.CancelledError:
            # 取消不是连接失败，但必须落一个确定状态，否则会停在 pending。
            # 重新抛出以保留取消语义。
            logger.warning(f"[MCP] Connection to '{name}' cancelled")
            self._clients[name] = FailedMCPServer(
                name=name, config=config, error="connection cancelled"
            )
            self._notify_state_change()
            raise
        except Exception as e:
            logger.error(f"[MCP Manager] Failed to connect to '{name}': {e}")
            self._clients[name] = FailedMCPServer(name=name, config=config, error=str(e))
            self._notify_state_change()
    
    async def _process_connected_client(self, client: ConnectedMCPServer):
        """处理已连接的客户端"""
        logger.info(f"[MCP Manager] Processing connected client: {client.name}")
        
        try:
            # 获取工具
            tools = await fetch_tools_for_client(client)
            self._update_tools_for_server(client.name, tools)
            
            # 获取命令
            commands = await fetch_commands_for_client(client)
            self._update_commands_for_server(client.name, commands)
            
            # 获取资源
            resources = await fetch_resources_for_client(client)
            if resources:
                self._resources[client.name] = resources
            
            # 注册 ListChanged 通知处理
            self._setup_list_changed_handlers(client)
            
            # 注册连接关闭处理
            if client._client and client._client.transport:
                # 启动连接监控
                self._start_connection_monitor(client)
            
        except Exception as e:
            logger.error(f"[MCP Manager] Failed to process client '{client.name}': {e}")
    
    # ==================== 通知处理 ====================
    
    def _setup_list_changed_handlers(self, client: ConnectedMCPServer):
        """设置 ListChanged 通知处理器"""
        mcp_client = client._client
        if not mcp_client or not mcp_client.transport:
            return
        
        transport = mcp_client.transport
        
        # tools/list_changed
        if client.capabilities.get("tools", {}).get("listChanged"):
            async def on_tools_changed(_params=None):
                logger.info(f"[MCP] tools/list_changed from '{client.name}'")
                try:
                    new_tools = await fetch_tools_for_client(client)
                    self._update_tools_for_server(client.name, new_tools)
                    self._notify_state_change()
                except Exception as e:
                    logger.error(f"[MCP] Failed to refresh tools for '{client.name}': {e}")
            
            transport.set_notification_handler("notifications/tools/list_changed", on_tools_changed)
        
        # prompts/list_changed
        if client.capabilities.get("prompts", {}).get("listChanged"):
            async def on_prompts_changed(_params=None):
                logger.info(f"[MCP] prompts/list_changed from '{client.name}'")
                try:
                    new_commands = await fetch_commands_for_client(client)
                    self._update_commands_for_server(client.name, new_commands)
                    self._notify_state_change()
                except Exception as e:
                    logger.error(f"[MCP] Failed to refresh prompts for '{client.name}': {e}")
            
            transport.set_notification_handler("notifications/prompts/list_changed", on_prompts_changed)
        
        # resources/list_changed
        if client.capabilities.get("resources", {}).get("listChanged"):
            async def on_resources_changed(_params=None):
                logger.info(f"[MCP] resources/list_changed from '{client.name}'")
                try:
                    new_resources = await fetch_resources_for_client(client)
                    self._resources[client.name] = new_resources
                    self._notify_state_change()
                except Exception as e:
                    logger.error(f"[MCP] Failed to refresh resources for '{client.name}': {e}")
            
            transport.set_notification_handler("notifications/resources/list_changed", on_resources_changed)
    
    def _start_connection_monitor(self, client: ConnectedMCPServer):
        """启动连接监控（检测断开并自动重连）"""
        async def monitor():
            mcp_client = client._client
            if not mcp_client or not mcp_client.transport:
                return
            
            # 发起一个定期心跳检查
            while mcp_client.is_connected and not self._canceled:
                await asyncio.sleep(30)  # 每 30 秒检查一次
                
                if not mcp_client.is_connected:
                    logger.warning(f"[MCP] Connection lost for '{client.name}', attempting reconnection")
                    await self._attempt_reconnect(client)
                    break
        
        task = asyncio.create_task(monitor())
        self._notification_tasks[client.name] = task
    
    # ==================== 重连 ====================
    
    async def _attempt_reconnect(self, old_client: ConnectedMCPServer):
        """尝试自动重连（指数退避）"""
        name = old_client.name
        config = old_client.config
        
        if is_mcp_server_disabled(name):
            logger.info(f"[MCP] Server '{name}' is disabled, skipping reconnection")
            return
        
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self._canceled or is_mcp_server_disabled(name):
                return
            
            # 更新为 pending 状态
            self._clients[name] = PendingMCPServer(
                name=name, 
                config=config,
                reconnect_attempt=attempt,
                max_reconnect_attempts=MAX_RECONNECT_ATTEMPTS
            )
            self._notify_state_change()
            
            try:
                # 清除旧缓存
                await clear_server_cache(name, config)
                
                # 重新连接
                new_client = await connect_to_server(name, config)
                
                if new_client.type == "connected" and isinstance(new_client, ConnectedMCPServer):
                    logger.info(f"[MCP] Reconnected to '{name}' on attempt {attempt}")
                    self._clients[name] = new_client
                    await self._process_connected_client(new_client)
                    self._notify_state_change()
                    return
                
                logger.warning(f"[MCP] Reconnection attempt {attempt}/{MAX_RECONNECT_ATTEMPTS} "
                             f"for '{name}' resulted in: {new_client.type}")
                
                if attempt == MAX_RECONNECT_ATTEMPTS:
                    self._clients[name] = new_client
                    self._notify_state_change()
                    return
                
            except Exception as e:
                logger.error(f"[MCP] Reconnection attempt {attempt}/{MAX_RECONNECT_ATTEMPTS} "
                           f"for '{name}' failed: {e}")
                
                if attempt == MAX_RECONNECT_ATTEMPTS:
                    self._clients[name] = FailedMCPServer(name=name, config=config, error=str(e))
                    self._notify_state_change()
                    return
            
            # 指数退避等待
            backoff_ms = min(INITIAL_BACKOFF_MS * (2 ** (attempt - 1)), MAX_BACKOFF_MS)
            logger.debug(f"[MCP] Waiting {backoff_ms}ms before next reconnection attempt for '{name}'")
            await asyncio.sleep(backoff_ms / 1000)
    
    async def reconnect_server(self, server_name: str) -> MCPServerConnection:
        """手动重连指定服务器"""
        if server_name not in self._configs:
            raise ValueError(f"MCP server '{server_name}' not found in configs")
        
        config = self._configs[server_name]
        logger.info(f"[MCP] Manual reconnection for '{server_name}'")
        
        # 清除缓存
        await clear_server_cache(server_name, config)
        
        # 设为 pending
        self._clients[server_name] = PendingMCPServer(name=server_name, config=config)
        self._notify_state_change()
        
        # 重新连接
        client = await connect_to_server(server_name, config)
        self._clients[server_name] = client
        
        if client.type == "connected" and isinstance(client, ConnectedMCPServer):
            await self._process_connected_client(client)
        
        self._notify_state_change()
        return client
    
    # ==================== 启用/禁用 ====================
    
    async def set_server_enabled_state(self, server_name: str, enabled: bool) -> bool:
        """显式设置服务器启用状态（幂等）。True=连接，False=断开。"""
        lock = self._toggle_locks.setdefault(server_name, asyncio.Lock())
        async with lock:
            if server_name not in self._configs:
                # 禁用的服务器在 initialize() 时被 get_enabled_configs() 排除，
                # 这里从配置层补回来，否则禁用后无法再次启用。
                from .mcp_config import get_mcp_config_manager
                recovered = get_mcp_config_manager().get_all_configs().get(server_name)
                if recovered is None:
                    raise ValueError(f"MCP server '{server_name}' not found")
                self._configs[server_name] = recovered

            if enabled == (not is_mcp_server_disabled(server_name)):
                # 已是目标状态。禁用态仍需回收工具：历史残留不会自愈，
                # 而 mcp.list 与 agent 工具定义都直接读 self._tools。
                if not enabled:
                    self._update_tools_for_server(server_name, [])
                    self._notify_state_change()
                return True

            config = self._configs[server_name]

            if enabled:
                set_mcp_server_enabled(server_name, True)
                self._clients[server_name] = PendingMCPServer(name=server_name, config=config)
                self._notify_state_change()
                await self._connect_and_process(server_name, config)
            else:
                set_mcp_server_enabled(server_name, False)
                current = self._clients.get(server_name)
                try:
                    if isinstance(current, ConnectedMCPServer) and current._client:
                        await current._client.close()
                except asyncio.CancelledError:
                    # 两种情况要区分：
                    # 1) 调用方真的取消了这个任务 -> 继续传播
                    # 2) MCP SDK 关闭 anyio 传输时自身抛出的 CancelledError -> 关闭已完成
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        logger.warning(
                            f"[MCP] Close of '{server_name}' cancelled by caller"
                        )
                        raise
                    logger.info(
                        f"[MCP] Close of '{server_name}' raised CancelledError from the "
                        f"SDK; treating it as a completed close"
                    )
                except Exception as e:
                    logger.warning(
                        f"[MCP] Closing '{server_name}' raised "
                        f"{type(e).__name__}: {e} (state still switched to disabled)"
                    )
                finally:
                    # 无论关闭成功、失败还是被取消，都要停监控并落 disabled 状态
                    task = self._notification_tasks.pop(server_name, None)
                    if task and not task.done():
                        task.cancel()
                    self._clients[server_name] = DisabledMCPServer(
                        name=server_name, config=config
                    )
                    # 断开后必须回收工具，否则 mcp.list 的角标与 agent 的工具
                    # 定义仍会暴露该服务器的工具，调用时必然 not connected。
                    self._update_tools_for_server(server_name, [])
                    self._notify_state_change()

            return True

    async def toggle_server(self, server_name: str) -> bool:
        """切换服务器启用/禁用状态"""
        return await self.set_server_enabled_state(
            server_name, is_mcp_server_disabled(server_name)
        )
    
    # ==================== 状态管理 ====================
    
    def _update_tools_for_server(self, server_name: str, new_tools: List[SerializedMCPTool]):
        """更新指定服务器的工具，同时注册 MCP 工具到并发系统"""
        # 先清除旧分类
        try:
            from ..tool_concurrency import unregister_mcp_tool_concurrency
            unregister_mcp_tool_concurrency(server_name)
        except ImportError:
            pass
        
        # 注册新工具的分类（基于 MCP 协议 annotations）
        try:
            from ..tool_concurrency import register_mcp_tool_concurrency
            for tool in new_tools:
                annotations = getattr(tool, 'annotations', {}) or {}
                register_mcp_tool_concurrency(
                    tool_full_name=tool.name,
                    read_only_hint=annotations.get('readOnlyHint'),
                    destructive_hint=annotations.get('destructiveHint')
                )
        except ImportError:
            pass
        
        # 一次性替换，避免读者观察到"旧工具已删、新工具未加"的中间态。
        # 同步执行：连接流程、reload、disable 都需要调用后立刻读到结果。
        prefix = f"mcp__{server_name.replace('-', '_').lower()}__"
        self._tools = [
            t for t in self._tools if not t.name.startswith(prefix)
        ] + list(new_tools)
    
    def _update_commands_for_server(self, server_name: str, new_commands: List[Dict[str, Any]]):
        """更新指定服务器的命令"""
        # 移除旧命令
        prefix = f"mcp__{server_name.replace('-', '_').lower()}"
        self._commands = [c for c in self._commands 
                         if not c.get("name", "").startswith(prefix)]
        
        # 添加新命令
        self._commands.extend(new_commands)
    
    def _notify_state_change(self):
        """通知状态变更"""
        for callback in self._on_state_change:
            try:
                callback(self.get_state())
            except Exception as e:
                logger.error(f"[MCP Manager] State change callback error: {e}")
    
    def on_state_change(self, callback: Callable):
        """注册状态变更回调"""
        self._on_state_change.append(callback)
    
    # ==================== 查询方法 ====================
    
    def get_state(self) -> MCPCliState:
        """获取当前状态"""
        clients = []
        for name, client in self._clients.items():
            serialized = SerializedMCPClient(
                name=name,
                type=client.type,
                error=getattr(client, 'error', None)
            )
            if hasattr(client, 'capabilities'):
                serialized.capabilities = client.capabilities  # type: ignore
            if hasattr(client, 'server_version'):
                serialized.server_version = client.server_version  # type: ignore
            clients.append(serialized)
        
        return MCPCliState(
            clients=clients,
            configs=self._configs,
            tools=self._tools,
            resources=self._resources,
            normalized_names=self._normalized_names,
        )
    
    def get_all_tools(self) -> List[Dict[str, Any]]:
        """获取所有工具（OpenAI 格式）"""
        return [t.to_openai_format() for t in self._tools]
    
    def get_tool_info(self, tool_name: str) -> Optional[SerializedMCPTool]:
        """获取工具信息"""
        for tool in self._tools:
            if tool.name == tool_name:
                return tool
        return None
    
    def is_mcp_tool(self, tool_name: str) -> bool:
        """检查是否是 MCP 工具"""
        return tool_name.startswith("mcp__")
    
    def get_server_for_tool(self, tool_name: str) -> Optional[ConnectedMCPServer]:
        """获取工具所属的服务器连接"""
        from .mcp_client import parse_mcp_tool_name
        
        parsed = parse_mcp_tool_name(tool_name)
        if not parsed:
            return None
        
        server_name = parsed[0]
        client = self._clients.get(server_name)
        
        if client and client.type == "connected" and isinstance(client, ConnectedMCPServer):
            return client
        return None
    
    def get_connected_client(self, server_name: str) -> Optional[ConnectedMCPServer]:
        """获取已连接的客户端"""
        client = self._clients.get(server_name)
        if client and client.type == "connected" and isinstance(client, ConnectedMCPServer):
            return client
        return None
    
    # ==================== 关闭 ====================
    
    async def shutdown(self):
        """关闭管理器"""
        logger.info("[MCP Manager] Shutting down...")
        self._canceled = True
        
        # 取消所有重连定时器
        for task in self._reconnect_timers.values():
            task.cancel()
        self._reconnect_timers.clear()
        
        # 关闭所有连接
        for name, client in self._clients.items():
            if isinstance(client, ConnectedMCPServer) and client._client:
                try:
                    await client._client.close()
                except Exception as e:
                    logger.error(f"[MCP Manager] Error closing '{name}': {e}")
        
        self._clients.clear()
        self._tools.clear()
        self._commands.clear()
        self._resources.clear()
        self._initialized = False
        logger.info("[MCP Manager] Shutdown complete")


# ==================== 全局实例 ====================

_manager: Optional[McpManager] = None
_manager_lock = asyncio.Lock()


def get_mcp_manager() -> McpManager:
    """获取全局 MCP 管理器（单例）"""
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager


async def init_mcp_manager(dynamic_configs: Dict[str, ScopedMcpServerConfig] = None, project_path: Optional[str] = None):
    """初始化全局 MCP 管理器"""
    global _manager
    async with _manager_lock:
        if _manager is None:
            _manager = McpManager()
        if not _manager._initialized:
            # 传入项目路径确保能找对 .mcp.json
            if project_path:
                from .mcp_config import get_mcp_config_manager
                cfg_mgr = get_mcp_config_manager()
                cfg_mgr.set_project_path(project_path)
            await _manager.initialize(dynamic_configs)
    return _manager


async def shutdown_mcp_manager():
    """关闭全局 MCP 管理器"""
    global _manager
    if _manager:
        await _manager.shutdown()
        _manager = None
