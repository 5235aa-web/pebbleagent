"""MCP tool routing."""

import logging
from typing import Any, Dict, Optional

from ..mcp.mcp_integration import McpToolExecutorFactory
from ..query_state_machine import ToolExecutionContext

logger = logging.getLogger(__name__)



_mcp_executor_factory: Optional[McpToolExecutorFactory] = None


class McpToolsMixin:

    async def _execute_mcp_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolExecutionContext = None
    ) -> str:
        """
        执行 MCP 工具
        
        通过 MCP 集成层路由工具调用到对应的 MCP 服务器。
        
        Args:
            tool_name: MCP 工具名（格式: mcp__server__tool）
            args: 工具参数
            context: 执行上下文
        
        Returns:
            工具执行结果字符串
        """
        global _mcp_executor_factory
        
        try:
            # 延迟初始化 MCP 执行器工厂
            if _mcp_executor_factory is None:
                _mcp_executor_factory = McpToolExecutorFactory()
            
            return await _mcp_executor_factory.execute_mcp_tool(
                tool_name=tool_name,
                args=args,
                context=context
            )
        except ImportError as e:
            return f"Error: MCP integration not available: {e}"
        except Exception as e:
            logger.error(f"[ToolExecutor] MCP tool execution failed for '{tool_name}': {e}")
            return f"Error executing MCP tool '{tool_name}': {str(e)}"
