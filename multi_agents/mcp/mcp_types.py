"""
MCP Types - MCP 类型定义

包含：
- 配置作用域 (ConfigScope)
- 传输类型 (TransportType)  
- 服务器配置 (McpServerConfig 及其变体)
- 连接状态 (MCPServerConnection 联合类型)
- 工具序列化类型
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union, Literal, Callable, Awaitable
from enum import Enum


# ==================== 配置作用域 ====================

class ConfigScope(str, Enum):
    """配置作用域 - 对应 Claude-Code 的 ConfigScope"""
    LOCAL = "local"           # 本地项目配置 (settings.local.json)
    USER = "user"             # 用户全局配置 (~/.claude/settings.json)
    PROJECT = "project"       # 项目配置 (.mcp.json, 从 CWD 向上遍历)
    DYNAMIC = "dynamic"       # 动态配置 (--mcp-config 或插件注入)
    ENTERPRISE = "enterprise" # 企业管理配置
    CLAUDEAI = "claudeai"     # claude.ai 连接器
    MANAGED = "managed"       # 托管设置


class TransportType(str, Enum):
    """传输类型 - 对应 Claude-Code 的 Transport"""
    STDIO = "stdio"           # 标准输入/输出（子进程）
    SSE = "sse"               # Server-Sent Events
    SSE_IDE = "sse-ide"       # IDE 扩展 SSE
    HTTP = "http"             # Streamable HTTP
    WS = "ws"                 # WebSocket
    WS_IDE = "ws-ide"         # IDE 扩展 WebSocket
    SDK = "sdk"               # SDK 进程内传输
    CLAUDEAI_PROXY = "claudeai-proxy"  # claude.ai 代理


# ==================== 服务器配置 ====================

@dataclass
class McpStdioServerConfig:
    """Stdio 服务器配置 - 对应 McpStdioServerConfigSchema"""
    command: str                                    # 启动命令
    args: List[str] = field(default_factory=list)   # 命令参数
    env: Optional[Dict[str, str]] = None            # 环境变量
    type: Optional[str] = None  # 'stdio' 或 None（向后兼容）


@dataclass
class McpOAuthConfig:
    """OAuth 配置"""
    client_id: Optional[str] = None
    callback_port: Optional[int] = None
    auth_server_metadata_url: Optional[str] = None
    xaa: Optional[bool] = None  # Cross-App Access


@dataclass
class McpSSEServerConfig:
    """SSE 服务器配置"""
    type: Literal["sse"] = "sse"
    url: str = ""
    headers: Optional[Dict[str, str]] = None
    headers_helper: Optional[str] = None
    oauth: Optional[McpOAuthConfig] = None


@dataclass
class McpSSEIDEServerConfig:
    """IDE SSE 服务器配置"""
    type: Literal["sse-ide"] = "sse-ide"
    url: str = ""
    ide_name: str = ""
    ide_running_in_windows: Optional[bool] = None


@dataclass
class McpWebSocketIDEServerConfig:
    """IDE WebSocket 服务器配置"""
    type: Literal["ws-ide"] = "ws-ide"
    url: str = ""
    ide_name: str = ""
    auth_token: Optional[str] = None
    ide_running_in_windows: Optional[bool] = None


@dataclass
class McpHTTPServerConfig:
    """HTTP 服务器配置"""
    type: Literal["http"] = "http"
    url: str = ""
    headers: Optional[Dict[str, str]] = None
    headers_helper: Optional[str] = None
    oauth: Optional[McpOAuthConfig] = None


@dataclass
class McpWebSocketServerConfig:
    """WebSocket 服务器配置"""
    type: Literal["ws"] = "ws"
    url: str = ""
    headers: Optional[Dict[str, str]] = None
    headers_helper: Optional[str] = None


@dataclass
class McpSdkServerConfig:
    """SDK 服务器配置"""
    type: Literal["sdk"] = "sdk"
    name: str = ""


@dataclass
class McpClaudeAIProxyServerConfig:
    """Claude.ai 代理服务器配置"""
    type: Literal["claudeai-proxy"] = "claudeai-proxy"
    url: str = ""
    id: str = ""


# 服务器配置联合类型
McpServerConfig = Union[
    McpStdioServerConfig,
    McpSSEServerConfig,
    McpSSEIDEServerConfig,
    McpWebSocketIDEServerConfig,
    McpHTTPServerConfig,
    McpWebSocketServerConfig,
    McpSdkServerConfig,
    McpClaudeAIProxyServerConfig,
]


@dataclass
class ScopedMcpServerConfig:
    """带作用域的服务器配置"""
    config: McpServerConfig
    scope: ConfigScope = ConfigScope.USER
    plugin_source: Optional[str] = None  # 插件来源标识
    
    @property
    def type(self) -> Optional[str]:
        """获取传输类型"""
        if hasattr(self.config, 'type'):
            return getattr(self.config, 'type')
        return None
    
    @property
    def name(self) -> str:
        """获取服务器名称"""
        if isinstance(self.config, McpSdkServerConfig):
            return self.config.name
        return ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {"scope": self.scope.value}
        config_dict = {}
        for key, value in self.config.__dict__.items():
            if value is not None:
                if isinstance(value, Enum):
                    config_dict[key] = value.value
                elif isinstance(value, dict):
                    config_dict[key] = value
                elif isinstance(value, list):
                    config_dict[key] = value
                else:
                    config_dict[key] = value
        result.update(config_dict)
        return result


@dataclass
class McpJsonConfig:
    """MCP JSON 配置文件结构"""
    mcp_servers: Dict[str, McpServerConfig] = field(default_factory=dict)


# ==================== 服务器连接状态 ====================

@dataclass
class ConnectedMCPServer:
    """已连接 MCP 服务器"""
    name: str
    type: Literal["connected"] = "connected"
    config: ScopedMcpServerConfig = field(default_factory=lambda: ScopedMcpServerConfig(
        config=McpStdioServerConfig(command="")
    ))
    capabilities: Dict[str, Any] = field(default_factory=dict)
    server_info: Optional[Dict[str, str]] = None  # {name, version}
    instructions: Optional[str] = None
    server_version: Optional[str] = None
    _client: Any = None  # MCP SDK Client 实例
    _transport: Any = None  # 传输层实例
    
    async def cleanup(self):
        """清理连接"""
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self._transport = None


@dataclass
class FailedMCPServer:
    """连接失败 MCP 服务器"""
    name: str
    type: Literal["failed"] = "failed"
    config: ScopedMcpServerConfig = field(default_factory=lambda: ScopedMcpServerConfig(
        config=McpStdioServerConfig(command="")
    ))
    error: Optional[str] = None


@dataclass
class NeedsAuthMCPServer:
    """需要认证 MCP 服务器"""
    name: str
    type: Literal["needs-auth"] = "needs-auth"
    config: ScopedMcpServerConfig = field(default_factory=lambda: ScopedMcpServerConfig(
        config=McpStdioServerConfig(command="")
    ))


@dataclass
class PendingMCPServer:
    """连接中 MCP 服务器"""
    name: str
    type: Literal["pending"] = "pending"
    config: ScopedMcpServerConfig = field(default_factory=lambda: ScopedMcpServerConfig(
        config=McpStdioServerConfig(command="")
    ))
    reconnect_attempt: Optional[int] = None
    max_reconnect_attempts: Optional[int] = None


@dataclass
class DisabledMCPServer:
    """已禁用 MCP 服务器"""
    name: str
    type: Literal["disabled"] = "disabled"
    config: ScopedMcpServerConfig = field(default_factory=lambda: ScopedMcpServerConfig(
        config=McpStdioServerConfig(command="")
    ))


MCPServerConnection = Union[
    ConnectedMCPServer,
    FailedMCPServer,
    NeedsAuthMCPServer,
    PendingMCPServer,
    DisabledMCPServer,
]


# ==================== 资源类型 ====================

@dataclass
class ServerResource:
    """服务器资源"""
    uri: str
    name: str
    server: str
    description: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None


# ==================== 序列化类型（用于 API 传输） ====================

@dataclass
class SerializedMCPTool:
    """序列化 MCP 工具"""
    name: str                           # 完全限定名 mcp__server__tool
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    is_mcp: bool = True
    original_tool_name: Optional[str] = None  # 原始未规范化的工具名
    server_name: Optional[str] = None         # 所属服务器名
    annotations: Dict[str, Any] = field(default_factory=dict)
    
    def to_openai_format(self) -> Dict[str, Any]:
        """转换为 OpenAI 工具格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema
            }
        }


@dataclass
class SerializedMCPClient:
    """序列化 MCP 客户端状态"""
    name: str
    type: str  # "connected" | "failed" | "needs-auth" | "pending" | "disabled"
    capabilities: Optional[Dict[str, Any]] = None
    server_version: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MCPCliState:
    """MCP CLI 状态"""
    clients: List[SerializedMCPClient] = field(default_factory=list)
    configs: Dict[str, ScopedMcpServerConfig] = field(default_factory=dict)
    tools: List[SerializedMCPTool] = field(default_factory=list)
    resources: Dict[str, List[ServerResource]] = field(default_factory=dict)
    normalized_names: Dict[str, str] = field(default_factory=dict)


# ==================== 工具调用结果 ====================

@dataclass
class MCPToolResult:
    """MCP 工具调用结果"""
    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    _meta: Optional[Dict[str, Any]] = None
    
    def get_text(self) -> str:
        """获取文本内容"""
        texts = []
        for block in self.content:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts)


# ==================== 辅助函数 ====================

def get_config_type(config: McpServerConfig) -> Optional[str]:
    """获取配置的传输类型"""
    if hasattr(config, 'type'):
        t = getattr(config, 'type')
        return t if t else "stdio"
    return "stdio"


def is_local_mcp_server(config: ScopedMcpServerConfig) -> bool:
    """检查是否是本地 MCP 服务器"""
    t = get_config_type(config.config)
    return t is None or t == "stdio" or t == "sdk"


def is_remote_mcp_server(config: ScopedMcpServerConfig) -> bool:
    """检查是否是远程 MCP 服务器"""
    return not is_local_mcp_server(config)
