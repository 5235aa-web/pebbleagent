"""
MCP (Model Context Protocol) - MCP 服务集成

基于 Claude-Code MCP 服务实现，将 MCP 协议集成到 Agent 系统中。

核心组件：
- mcp_types: 类型定义（配置、连接状态、工具）
- mcp_config: 配置管理（多作用域加载、验证、策略）
- mcp_client: 核心客户端（连接、工具调用、资源获取）
- mcp_manager: 连接管理器（生命周期、批量连接、重连）
- mcp_integration: 与现有系统集成

使用方式：
    from agent_core.mcp import get_mcp_manager
    manager = get_mcp_manager()
    await manager.initialize()
    tools = manager.get_all_tools()
"""

from .mcp_types import (
    # Config scope
    ConfigScope,
    TransportType,
    # Server configs
    McpStdioServerConfig,
    McpSSEServerConfig,
    McpHTTPServerConfig,
    McpWebSocketServerConfig,
    McpServerConfig,
    ScopedMcpServerConfig,
    McpJsonConfig,
    # Connection states
    MCPServerConnection,
    ConnectedMCPServer,
    FailedMCPServer,
    NeedsAuthMCPServer,
    PendingMCPServer,
    DisabledMCPServer,
    # Tool types
    SerializedMCPTool,
    SerializedMCPClient,
    MCPCliState,
    ServerResource,
)

from .mcp_config import (
    McpConfigManager,
    get_mcp_config_manager,
    add_mcp_config,
    remove_mcp_config,
    get_mcp_config_by_name,
    get_all_mcp_configs,
    is_mcp_server_disabled,
    set_mcp_server_enabled,
)

from .mcp_client import (
    McpClient,
    connect_to_server,
    fetch_tools_for_client,
    fetch_resources_for_client,
    fetch_commands_for_client,
    call_mcp_tool,
    ensure_connected_client,
    get_mcp_tools_commands_and_resources,
    build_mcp_tool_name,
    parse_mcp_tool_name,
)

from .mcp_manager import (
    McpManager,
    get_mcp_manager,
    init_mcp_manager,
    shutdown_mcp_manager,
)

from .mcp_integration import (
    McpToolWrapper,
    McpToolExecutorFactory,
    is_mcp_tool,
    register_mcp_tools,
    mcp_tool_executor_factory,
)

__all__ = [
    # Types
    "ConfigScope",
    "TransportType",
    "McpStdioServerConfig",
    "McpSSEServerConfig",
    "McpHTTPServerConfig",
    "McpWebSocketServerConfig",
    "McpServerConfig",
    "ScopedMcpServerConfig",
    "McpJsonConfig",
    "MCPServerConnection",
    "ConnectedMCPServer",
    "FailedMCPServer",
    "NeedsAuthMCPServer",
    "PendingMCPServer",
    "DisabledMCPServer",
    "SerializedMCPTool",
    "SerializedMCPClient",
    "MCPCliState",
    "ServerResource",
    # Config
    "McpConfigManager",
    "get_mcp_config_manager",
    "add_mcp_config",
    "remove_mcp_config",
    "get_mcp_config_by_name",
    "get_all_mcp_configs",
    "is_mcp_server_disabled",
    "set_mcp_server_enabled",
    # Client
    "McpClient",
    "connect_to_server",
    "fetch_tools_for_client",
    "fetch_resources_for_client",
    "fetch_commands_for_client",
    "call_mcp_tool",
    "ensure_connected_client",
    "get_mcp_tools_commands_and_resources",
    "build_mcp_tool_name",
    "parse_mcp_tool_name",
    # Manager
    "McpManager",
    "get_mcp_manager",
    "init_mcp_manager",
    "shutdown_mcp_manager",
    # Integration
    "McpToolWrapper",
    "McpToolExecutorFactory",
    "is_mcp_tool",
    "register_mcp_tools",
    "mcp_tool_executor_factory",
]
