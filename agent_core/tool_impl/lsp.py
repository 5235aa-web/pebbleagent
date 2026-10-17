"""Language-server code intelligence tools."""

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



_lsp_client = None


_lsp_initialized = False


class LspToolsMixin:

    async def _lsp_diagnostics_after_write(self, file_path: str) -> str:
        """
        写入文件后自动获取 LSP 诊断，拼接到工具返回值中。
        
        完全静默——LSP 不可用时返回空字符串，不抛出异常。
        仅对代码文件类型触发诊断。
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".json"):
            return ""
        
        try:
            from ..lsp_client import get_lsp_client, ensure_lsp_initialized
            client = await ensure_lsp_initialized(self._current_project_path)
            if client is None:
                return ""
            
            await client.touch_file(file_path)
            diagnostics = await client.wait_for_diagnostics(file_path, timeout=3.0)
            report = client.report_diagnostics(file_path, diagnostics)
            
            if report:
                return f"\n\nLSP 检测到以下错误，请修复：\n{report}"
            return ""
        except Exception:
            return ""


    async def _ensure_lsp_initialized(self) -> Optional[Any]:
        """
        确保 LSP 客户端已初始化
        
        延迟初始化模式：首次调用时启动 LSP 服务器。
        后续调用复用已初始化的客户端。
        
        Returns:
            LspClient 实例，或 None（初始化失败）
        """
        global _lsp_client, _lsp_initialized
        
        if _lsp_initialized and _lsp_client and _lsp_client.is_initialized:
            return _lsp_client
        
        try:
            from ..lsp_client import LspClient, get_lsp_client
            
            _lsp_client = get_lsp_client()
            
            if not _lsp_client.is_initialized:
                # 使用当前项目路径作为 LSP 根目录
                project_path = self._current_project_path
                if not os.path.isabs(project_path):
                    project_path = os.path.abspath(project_path)
                
                success = await _lsp_client.initialize(project_path)
                if not success:
                    logger.warning(
                        f"[ToolExecutor] LSP initialization failed for {project_path}. "
                        f"Install pyright (npm install -g pyright) or jedi-language-server (pip install jedi-language-server)"
                    )
                    _lsp_initialized = True  # 标记已尝试，避免反复重试
                    return None
                
                logger.info(
                    f"[ToolExecutor] LSP initialized: {_lsp_client._state.server_display_name} "
                    f"(PID={_lsp_client._process.pid if _lsp_client._process else 'unknown'})"
                )
            
            _lsp_initialized = True
            return _lsp_client
            
        except ImportError as e:
            logger.error(f"[ToolExecutor] LSP client module not available: {e}")
            _lsp_initialized = True  # 标记已尝试
            return None
        except Exception as e:
            logger.error(f"[ToolExecutor] Failed to initialize LSP client: {e}")
            _lsp_initialized = True
            return None


    def _resolve_lsp_file_path(self, file_path: str) -> Optional[str]:
        """
        解析 LSP 文件路径
        
        支持相对路径（相对于项目目录）和绝对路径。
        检查文件是否存在且是普通文件。
        
        Args:
            file_path: 用户提供的文件路径
        
        Returns:
            解析后的绝对路径，或 None（无效路径）
        """
        if not file_path:
            return None
        
        resolved = self._resolve_path(file_path)
        
        if not os.path.exists(resolved):
            return None
        
        if not os.path.isfile(resolved):
            return None
        
        return os.path.abspath(resolved)


    async def _tool_lsp_go_to_definition(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 跳转到定义"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            locations = await client.go_to_definition(resolved, line, character)
            return self._format_lsp_locations("定义", locations, self._current_project_path)
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP go_to_definition 失败: {e}"


    async def _tool_lsp_find_references(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 查找引用"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            locations = await client.find_references(resolved, line, character)
            return self._format_lsp_locations("引用", locations, self._current_project_path)
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP find_references 失败: {e}"


    async def _tool_lsp_hover(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 悬停信息"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            text = await client.hover(resolved, line, character)
            if text:
                return f"Hover 信息 ({os.path.basename(resolved)}:{line}:{character}):\n\n{text}"
            else:
                return f"该位置 ({os.path.basename(resolved)}:{line}:{character}) 没有可用的悬停信息。可能光标不处于任何符号上，或 LSP 服务器尚未完成索引。"
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP hover 失败: {e}"


    async def _tool_lsp_document_symbol(
        self, file_path: str
    ) -> str:
        """LSP: 文档符号"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            symbols = await client.document_symbol(resolved)
            if not symbols:
                return f"文件 {os.path.basename(resolved)} 中没有找到符号。可能文件为空或 LSP 服务器尚未完成索引。"
            
            return self._format_lsp_symbols("文档符号", symbols, self._current_project_path)
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP document_symbol 失败: {e}"


    async def _tool_lsp_workspace_symbol(
        self, query: str
    ) -> str:
        """LSP: 工作区符号搜索"""
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            symbols = await client.workspace_symbol(query)
            if not symbols:
                if query:
                    return f"在项目中未找到匹配 '{query}' 的符号。"
                else:
                    return "项目中未找到任何符号。可能 LSP 服务器尚未完成项目索引。"
            
            return self._format_lsp_symbols(
                f"工作区符号搜索" + (f" (查询: '{query}')" if query else ""),
                symbols,
                self._current_project_path
            )
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP workspace_symbol 失败: {e}"


    async def _tool_lsp_go_to_implementation(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 跳转到实现"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            locations = await client.go_to_implementation(resolved, line, character)
            return self._format_lsp_locations("实现", locations, self._current_project_path)
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP go_to_implementation 失败: {e}"


    async def _tool_lsp_prepare_call_hierarchy(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 准备调用层次"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            items = await client.prepare_call_hierarchy(resolved, line, character)
            if not items:
                return f"该位置 ({os.path.basename(resolved)}:{line}:{character}) 没有可用的调用层次。可能光标不处于函数/方法上。"
            
            return self._format_lsp_call_hierarchy_items("调用层次入口", items, self._current_project_path)
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP prepare_call_hierarchy 失败: {e}"


    async def _tool_lsp_incoming_calls(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 传入调用"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            calls = await client.incoming_calls(resolved, line, character)
            if not calls:
                return f"未找到传入调用（没有其他函数调用此函数）。"
            
            return self._format_lsp_call_hierarchy_calls("传入调用", calls, self._current_project_path, direction="incoming")
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP incoming_calls 失败: {e}"


    async def _tool_lsp_outgoing_calls(
        self, file_path: str, line: int, character: int
    ) -> str:
        """LSP: 传出调用"""
        resolved = self._resolve_lsp_file_path(file_path)
        if not resolved:
            return f"Error: 文件不存在或不是有效文件: {file_path}"
        
        if line <= 0 or character <= 0:
            return "Error: line 和 character 必须大于 0"
        
        client = await self._ensure_lsp_initialized()
        if not client:
            return self._lsp_not_available_message()
        
        try:
            calls = await client.outgoing_calls(resolved, line, character)
            if not calls:
                return f"未找到传出调用（此函数不调用任何其他函数）。"
            
            return self._format_lsp_call_hierarchy_calls("传出调用", calls, self._current_project_path, direction="outgoing")
        except ConnectionError as e:
            return f"Error: LSP 服务器未连接: {e}"
        except asyncio.TimeoutError:
            return "Error: LSP 请求超时"
        except Exception as e:
            return f"Error: LSP outgoing_calls 失败: {e}"


    def _lsp_not_available_message(self) -> str:
        """LSP 不可用时的提示消息"""
        return (
            "LSP 服务器不可用。\n\n"
            "请安装以下任一 LSP 服务器：\n"
            "1. Pyright（推荐，功能最全面）: npm install -g pyright\n"
            "2. Jedi Language Server: pip install jedi-language-server\n"
            "3. Python LSP Server: pip install python-lsp-server\n\n"
            "安装后重新启动对话即可使用 LSP 功能。"
        )


    def _format_lsp_locations(
        self, label: str, locations: List[Any], project_path: str
    ) -> str:
        """格式化 LSP 位置列表"""
        from ..lsp_client import LspLocation, uri_to_relative_path
        
        if not locations:
            return f"未找到{label}。可能光标不处于符号上，或 LSP 服务器尚未完成索引。"
        
        if len(locations) == 1:
            loc = locations[0]
            rel_path = uri_to_relative_path(loc.uri, project_path)
            return f"{label}位于: {rel_path}:{loc.line}:{loc.character}"
        
        # 按文件分组
        by_file: Dict[str, List[Any]] = {}
        for loc in locations:
            rel_path = uri_to_relative_path(loc.uri, project_path)
            if rel_path not in by_file:
                by_file[rel_path] = []
            by_file[rel_path].append(loc)
        
        lines = [f"找到 {len(locations)} 个{label}，分布在 {len(by_file)} 个文件中:"]
        
        for file_path, locs in by_file.items():
            lines.append(f"\n{file_path}:")
            for loc in locs:
                lines.append(f"  行 {loc.line}:{loc.character}")
        
        return "\n".join(lines)


    def _format_lsp_symbols(
        self, label: str, symbols: List[Any], project_path: str
    ) -> str:
        """格式化 LSP 符号列表"""
        from ..lsp_client import LspSymbolInfo, uri_to_relative_path
        
        if not symbols:
            return f"未找到符号。"
        
        # 按文件分组
        by_file: Dict[str, List[Any]] = {}
        for sym in symbols:
            uri = getattr(sym, 'uri', '')
            rel_path = uri_to_relative_path(uri, project_path) if uri else project_path
            if rel_path not in by_file:
                by_file[rel_path] = []
            by_file[rel_path].append(sym)
        
        lines = [f"{label}: 共 {len(symbols)} 个符号"]
        
        for file_path, syms in by_file.items():
            lines.append(f"\n{file_path}:")
            for sym in syms:
                name = getattr(sym, 'name', '???')
                kind_name = getattr(sym, 'kind_name', '')
                line = getattr(sym, 'line', 0)
                container = getattr(sym, 'container_name', None)
                detail = getattr(sym, 'detail', None)
                
                symbol_line = f"  {name} ({kind_name}) - 行 {line}"
                if container:
                    symbol_line += f" in {container}"
                if detail:
                    symbol_line += f" [{detail}]"
                
                lines.append(symbol_line)
        
        return "\n".join(lines)


    def _format_lsp_call_hierarchy_items(
        self, label: str, items: List[Any], project_path: str
    ) -> str:
        """格式化调用层次入口项"""
        from ..lsp_client import LspCallHierarchyItem, uri_to_relative_path
        
        if not items:
            return "未找到调用层次项。"
        
        if len(items) == 1:
            item = items[0]
            name = getattr(item, 'name', '???')
            kind_name = getattr(item, 'kind_name', '')
            rel_path = uri_to_relative_path(item.uri, project_path)
            line = getattr(item, 'line', 0)
            detail = getattr(item, 'detail', None)
            
            result = f"{label}: {name} ({kind_name}) - {rel_path}:{line}"
            if detail:
                result += f" [{detail}]"
            return result
        
        lines = [f"找到 {len(items)} 个{label}:"]
        for item in items:
            name = getattr(item, 'name', '???')
            kind_name = getattr(item, 'kind_name', '')
            rel_path = uri_to_relative_path(item.uri, project_path)
            line = getattr(item, 'line', 0)
            lines.append(f"  {name} ({kind_name}) - {rel_path}:{line}")
        
        return "\n".join(lines)


    def _format_lsp_call_hierarchy_calls(
        self, label: str, calls: List[Any], project_path: str, direction: str
    ) -> str:
        """格式化调用层次调用关系"""
        from ..lsp_client import LspCallHierarchyCall, LspCallHierarchyItem, uri_to_relative_path
        
        if not calls:
            return f"未找到{label}。"
        
        # 按文件分组
        by_file: Dict[str, List[Any]] = {}
        for call in calls:
            item = call.from_item if direction == "incoming" else call.to_item
            if not item:
                continue
            rel_path = uri_to_relative_path(item.uri, project_path)
            if rel_path not in by_file:
                by_file[rel_path] = []
            by_file[rel_path].append(call)
        
        lines = [f"找到 {len(calls)} 个{label}:"]
        
        for file_path, file_calls in by_file.items():
            lines.append(f"\n{file_path}:")
            for call in file_calls:
                item = call.from_item if direction == "incoming" else call.to_item
                if not item:
                    continue
                
                name = getattr(item, 'name', '???')
                kind_name = getattr(item, 'kind_name', '')
                line = getattr(item, 'line', 0)
                
                call_line = f"  {name} ({kind_name}) - 行 {line}"
                
                # 显示调用点
                from_ranges = getattr(call, 'from_ranges', [])
                if from_ranges:
                    call_sites = []
                    for r in from_ranges:
                        if isinstance(r, dict):
                            start = r.get("start", {})
                            call_sites.append(f"{start.get('line', 0) + 1}:{start.get('character', 0) + 1}")
                    if call_sites:
                        if direction == "incoming":
                            call_line += f" [调用位置: {', '.join(call_sites)}]"
                        else:
                            call_line += f" [从此处调用: {', '.join(call_sites)}]"
                
                lines.append(call_line)
        
        return "\n".join(lines)
