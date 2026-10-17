"""
MCP Integration - MCP 与现有系统的集成

桥梁层，将 MCP 系统与现有的 Agent 工具系统集成。

功能：
- MCP 工具动态注册到工具定义列表
- MCP 工具执行器工厂
- 与 ToolOrchestrator / ToolExecutor 的适配
"""

import asyncio
import logging
import time
from typing import Dict, List, Any, Optional

from .mcp_manager import get_mcp_manager
from .mcp_client import (
    parse_mcp_tool_name,
    call_mcp_tool,
    ensure_connected_client,
)
from .mcp_types import (
    SerializedMCPTool,
)

logger = logging.getLogger(__name__)


# ==================== MCP 工具执行器 ====================

class McpToolWrapper:
    """
    MCP 工具包装器
    
    将 MCP 工具包装为与现有工具系统兼容的形式。
    """
    
    def __init__(self, tool: SerializedMCPTool, manager=None):
        self._tool = tool
        self._manager = manager or get_mcp_manager()
    
    @property
    def name(self) -> str:
        return self._tool.name
    
    @property
    def description(self) -> str:
        return self._tool.description
    
    @property
    def parameters(self) -> Dict[str, Any]:
        return self._tool.input_schema
    
    @property
    def is_mcp(self) -> bool:
        return True
    
    def to_openai_format(self) -> Dict[str, Any]:
        """转换为 OpenAI 工具格式"""
        return self._tool.to_openai_format()
    
    async def execute(self, args: Dict[str, Any], context=None) -> str:
        """
        执行 MCP 工具
        
        Args:
            args: 工具参数
            context: 执行上下文（可以是 ToolExecutionContext）
        
        Returns:
            执行结果字符串
        """
        parsed = parse_mcp_tool_name(self._tool.name)
        if not parsed:
            return f"Error: Invalid MCP tool name: {self._tool.name}"
        
        server_name, tool_name = parsed
        
        # 获取服务器连接
        server = self._manager.get_connected_client(server_name)
        if not server:
            return f"Error: MCP server '{server_name}' is not connected"
        
        try:
            # 确保连接有效
            server = await ensure_connected_client(server)
            
            # 调用工具
            result = await call_mcp_tool(server, tool_name, args)
            
            if result.is_error:
                return f"Error: {result.get_text()}"
            
            return result.get_text()
            
        except Exception as e:
            logger.error(f"[MCP Integration] Tool execution failed: {e}")
            return f"Error executing MCP tool '{self._tool.name}': {str(e)}"


# ==================== 工具注册辅助函数 ====================

def register_mcp_tools(existing_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 MCP 工具合并到现有工具列表中
    
    Args:
        existing_tools: 现有的 OpenAI 格式工具列表
    
    Returns:
        合并后的工具列表（原工具 + MCP 工具）
    """
    manager = get_mcp_manager()
    mcp_tools = manager.get_all_tools()
    
    # MCP 工具追加到最后
    result = list(existing_tools)
    result.extend(mcp_tools)
    
    return result


def get_mcp_tool_wrappers() -> List[McpToolWrapper]:
    """获取所有 MCP 工具包装器"""
    manager = get_mcp_manager()
    state = manager.get_state()
    
    wrappers = []
    for tool in state.tools:
        wrappers.append(McpToolWrapper(tool, manager))
    
    return wrappers


def find_mcp_tool(tool_name: str) -> Optional[McpToolWrapper]:
    """查找 MCP 工具包装器"""
    manager = get_mcp_manager()
    tool = manager.get_tool_info(tool_name)
    if tool:
        return McpToolWrapper(tool, manager)
    return None


def is_mcp_tool(tool_name: str) -> bool:
    """检查是否是 MCP 工具"""
    return tool_name.startswith("mcp__")


# ==================== 执行器工厂 ====================

class McpToolExecutorFactory:
    """
    MCP 工具执行器工厂
    
    为 tool_executor_impl 提供 MCP 工具的执行逻辑。
    """
    
    def __init__(self, manager=None):
        self._manager = manager or get_mcp_manager()
    
    async def execute_mcp_tool(self, tool_name: str, args: Dict[str, Any], 
                               context=None, timeout_ms: int = 300_000) -> str:
        """
        执行 MCP 工具
        
        Args:
            tool_name: 完全限定的 MCP 工具名 (mcp__server__tool)
            args: 工具参数
            context: ToolExecutionContext（含 cancel_event）
            timeout_ms: 超时时间（毫秒）
        """
        parsed = parse_mcp_tool_name(tool_name)
        if not parsed:
            return f"Error: Invalid MCP tool name '{tool_name}'"
        
        server_name, original_tool_name = parsed
        
        # 获取取消事件（如果 context 提供了）
        cancel_event = None
        if context and hasattr(context, 'cancel_event'):
            cancel_event = context.cancel_event
            # 执行前先检查是否已被取消
            if cancel_event.is_set():
                raise asyncio.CancelledError("Tool execution cancelled by user")
        
        # 获取服务器（工具名中 - 被 normalize 为 _，反向尝试原始名）
        server = self._manager.get_connected_client(server_name)
        if not server:
            alt = server_name.replace('_', '-')
            if alt != server_name:
                server = self._manager.get_connected_client(alt)
        if not server:
            return f"Error: MCP server '{server_name}' is not connected"
        
        try:
            # 确保连接
            server = await ensure_connected_client(server)
            
            # 调用工具（传递取消事件和超时）
            start_time = time.time()
            result = await call_mcp_tool(server, original_tool_name, args, cancel_event=cancel_event)
            elapsed = int((time.time() - start_time) * 1000)
            
            # 格式化输出
            if result.is_error:
                error_text = result.get_text()
                logger.warning(f"[MCP] Tool '{tool_name}' returned error: {error_text[:200]}")
                return f"Error ({server_name}/{original_tool_name}, {elapsed}ms): {error_text}"
            
            text = result.get_text()
            if not text:
                # 尝试从 content blocks 提取文本
                for block in result.content:
                    if block.get("type") == "text":
                        text += block.get("text", "") + "\n"
                    elif block.get("type") == "resource":
                        text += f"[Resource: {block.get('resource', {}).get('uri', 'unknown')}]\n"
                    elif block.get("type") == "image":
                        text += "[Image content]\n"
            
            return text or "(empty result)"
            
        except Exception as e:
            logger.error(f"[MCP Integration] Failed to execute '{tool_name}': {e}")
            return f"Error executing MCP tool '{tool_name}': {str(e)}"


def mcp_tool_executor_factory() -> McpToolExecutorFactory:
    """创建 MCP 工具执行器工厂"""
    manager = get_mcp_manager()
    return McpToolExecutorFactory(manager)
