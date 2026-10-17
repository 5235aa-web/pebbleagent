"""MCP tool routing."""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..truncation_manager import truncate_output
from ..code_network_guardV2 import (
    scan_code_for_scraping_behavior,
    get_rate_limiter,
    get_path_depth_guard,
    get_download_guard,
    reset_all_guards,
)
from ..code_destructive_guard import scan_code_for_destructive_behavior

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
