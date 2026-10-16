"""
Tool Executor Implementation - 工具执行器实现

权限检查集成：
- 所有工具执行前进行权限检查
- 路径边界检查（防止越权访问）
- 完整的审计日志记录
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
import uuid
from typing import Dict, Any, List, Optional
from pathlib import Path

from .query_state_machine import ToolExecutionContext
from .tool_orchestrator import ToolExecutor
from .mcp.mcp_integration import is_mcp_tool, McpToolExecutorFactory
from .agent_loader import check_agent_tool_policy
from .permission_enforcer import PermissionEnforcer, PermissionCheckError, PathBoundaryError
from .electron_bridge import get_bridge, ElectronBridgeError, is_bridge_available
import httpx
from .truncation_manager import truncate_output
from .code_network_guardV2 import (
    scan_code_for_scraping_behavior,
    get_rate_limiter,
    get_path_depth_guard,
    get_download_guard,
    reset_all_guards,
)

# LSP 客户端（延迟导入，避免循环依赖和初始化时阻塞）
_lsp_client = None
_lsp_initialized = False

logger = logging.getLogger(__name__)

# MCP 工具执行器工厂（延迟初始化）
_mcp_executor_factory: Optional[McpToolExecutorFactory] = None

# EasyOCR Reader 全局缓存（延迟初始化）
_easyocr_reader = None
_easyocr_languages = None

# ==================== search_files 加固（2026-10-15） ====================
# 原实现为同步 os.walk + 全量读文件：无剪枝、无超时、无上限，content 模式
# 扫描本项目实测需读取 3GB 数据（32s+）且阻塞事件循环。加固为：
# 线程池执行 + 目录剪枝 + 流式分块搜索 + 时间预算 + 结果上限。

_SEARCH_SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv",
    "dist", "build", "target", ".next", ".nuxt", ".output", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "htmlcov",
}
_SEARCH_SKIP_EXTS = {
    # 可执行 / 库 / 编译产物
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pkg", ".pyz", ".pyc", ".pyo",
    ".rlib", ".rmeta", ".lib", ".obj", ".o", ".a", ".class", ".jar",
    # 归档 / 打包 / 安装介质
    ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".tar", ".whl", ".msi", ".iso", ".nupkg",
    # 数据库 / 二进制数据
    ".db", ".sqlite", ".sqlite3", ".mdb", ".pdb", ".dmp",
    # 媒体
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
    ".mp3", ".mp4", ".wav", ".flac", ".avi", ".mkv", ".mov", ".webm",
    # 字体
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # 文档（压缩容器格式，文本搜索无意义）
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
}
_SEARCH_CHUNK_BYTES = 1024 * 1024    # 流式读取块大小（1MB）
_SEARCH_MAX_RESULTS = 200            # 结果上限（达到即早退）
_SEARCH_TIME_BUDGET = 30.0           # 内部时间预算（秒，每块检查）
_SEARCH_OUTER_TIMEOUT = 35.0         # 外部兜底超时（秒，防单次 IO 卡死）


def _stream_search(filepath: str, regex, pattern: str, deadline: float) -> tuple:
    """
    search_files 流式分块搜索：恒定内存（~块大小），支持任意大小文件。

    返回 (matched, is_binary, timed_out)：
    - 二进制检测：任意块含 NUL 字节即终止（与 ripgrep 语义对齐）
    - 跨块匹配：保留尾部 overlap（pattern 长度-1）与下一块拼接后搜索
    - 编码：增量 UTF-8 解码器，正确处理跨块切分的多字节字符
    - 时间：每块检查 deadline，超时返回 (False, False, True)
    """
    import codecs
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    overlap = max(0, len(pattern) - 1)
    tail = ""
    try:
        with open(filepath, "rb") as f:
            while True:
                if time.monotonic() > deadline:
                    return False, False, True
                raw = f.read(_SEARCH_CHUNK_BYTES)
                if not raw:
                    break
                if 0 in raw:  # NUL 字节检测：判定为二进制文件
                    return False, True, False
                window = tail + decoder.decode(raw)
                if regex.search(window):
                    return True, False, False
                if overlap:
                    tail = window[-overlap:]
    except OSError:
        return False, False, False
    return False, False, False

def _parse_bing_search_results(html: str) -> list:
    """
    从 Bing 搜索结果页 HTML 中提取结构化结果。
    
    多层策略递进（Bing 不定期改 HTML，多层次兜底确保解析成功率）：
    0. html.parser 标准库 DOM 解析（韧性最强，不依赖 class 名）
    1. <li class="b_algo"> 结构（经典 Bing）
    2. <li class="b_ans"> 结构（Bing 答案卡片）
    3. 通用链接提取（tilk 类 / 任意 <a> 过滤）
    4. 文本正则回退（从纯文本中提取「标题 - 描述」对）
    """
    import re
    from html.parser import HTMLParser
    results = []
    seen_urls = set()
    
    def add_result(url: str, title: str, snippet: str = ""):
        nonlocal seen_urls
        if not url or not title:
            return
        # 清理 Bing 追踪包装 URL
        url = re.sub(r'^/url\?q=', '', url)
        url = url.split('&')[0] if '&' in url else url
        url = re.sub(r'^https?://[^/]+/url\?q=', '', url)
        url = url.split('&')[0] if '&' in url else url
        # 去重
        norm_url = url.rstrip('/').lower()
        if norm_url in seen_urls:
            return
        # 过滤内部/无用链接
        for skip in ["bing.com", "microsoft.com", "msn.com", "live.com",
                      "javascript:", "#", "go.microsoft.com"]:
            if skip in norm_url:
                return
        # 跳过已知广告/跟踪域名
        for ad in ["doubleclick.net", "googleadservices.com", "googlesyndication.com"]:
            if ad in norm_url:
                return
        seen_urls.add(norm_url)
        title = re.sub(r'\s+', ' ', title).strip()
        snippet = re.sub(r'\s+', ' ', snippet).strip() if snippet else ""
        results.append({"title": title, "url": url, "snippet": snippet})
    
    def strip_html(text: str) -> str:
        return re.sub(r'<[^>]+>', '', text).strip()
    
    # === Strategy 0: html.parser 标准库 DOM 解析 ===
    # 不依赖特定 class 名，韧性最高
    if not results:
        class BingResultParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self._in_link = False
                self._in_p = False
                self._current_tag = None
                self._current_link_href = ""
                self._current_link_text = ""
                self._current_snippet = ""
                self._parse_link = False
                self._tag_stack = []
            
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                self._tag_stack.append(tag)
                
                is_result_link = (tag == 'a' and 'href' in attrs_dict)
                if is_result_link:
                    href = attrs_dict['href']
                    if href.startswith(('http://', 'https://', '/url?q=')):
                        self._in_link = True
                        self._current_link_href = href
                        self._current_link_text = ""
                        self._current_snippet = ""
                
                if tag == 'p':
                    self._in_p = True
                    self._current_snippet = ""
            
            def handle_data(self, data):
                if self._in_link:
                    self._current_link_text += data
                if self._in_p:
                    self._current_snippet += data
            
            def handle_endtag(self, tag):
                self._tag_stack.pop()
                if tag == 'a' and self._in_link:
                    self._in_link = False
                    # 仅在链接文本足够长时才认为是搜索结果
                    text = self._current_link_text.strip()
                    href = self._current_link_href
                    if text and len(text) > 5:
                        # 检查父级是否在 b_algo 或 b_ans 内（有摘要提示更可靠）
                        add_result(href, text)
                if tag == 'p':
                    self._in_p = False
        
        parser = BingResultParser()
        try:
            parser.feed(html)
        except Exception:
            pass  # HTML 解析失败，继续下个策略
    
    # === Strategy 1: <li class="b_algo"> (经典 Bing) ===
    if not results:
        algo_blocks = re.findall(
            r'<li[^>]*class="b_algo"[^>]*>(.*?)</li>',
            html, re.DOTALL | re.IGNORECASE
        )
        for block in algo_blocks:
            link_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_match:
                link_match = re.search(r'<a[^>]*href="(/url\?q=[^"&]+)[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_match:
                continue
            url = link_match.group(1)
            title = strip_html(link_match.group(2))
            snippet = ""
            p_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if p_match:
                snippet = strip_html(p_match.group(1))
            if not snippet:
                caption_match = re.search(r'<div[^>]*class="b_caption"[^>]*>(.*?)</div>', block, re.DOTALL)
                if caption_match:
                    snippet = strip_html(caption_match.group(1))
            if not snippet:
                snippet_match = re.search(r'<span[^>]*class="b_snip"[^>]*>(.*?)</span>', block, re.DOTALL)
                if snippet_match:
                    snippet = strip_html(snippet_match.group(1))
            add_result(url, title, snippet)
            if len(results) >= 15:
                break
    
    # === Strategy 2: <li class="b_ans"> (Bing 答案卡片) ===
    if not results:
        ans_blocks = re.findall(
            r'<li[^>]*class="b_ans"[^>]*>(.*?)</li>',
            html, re.DOTALL | re.IGNORECASE
        )
        for block in ans_blocks:
            link_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if link_match:
                add_result(link_match.group(1), strip_html(link_match.group(2)))
    
    # === Strategy 3: 通用链接提取（适用于新版 Bing UI） ===
    if not results:
        all_links = re.findall(
            r'<a[^>]*href="(https?://(?!www\.bing\.com|www\.microsoft\.com|www\.msn\.com)[^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for url, title_html in all_links[:30]:
            title = strip_html(title_html)
            if title and len(title) > 5:
                add_result(url, title)
                if len(results) >= 15:
                    break
    
    # === Strategy 4: 从纯文本用正则回退 ===
    if not results:
        text = re.sub(r'<[^>]+>', '\n', html)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        i = 0
        while i < len(lines) - 1 and len(results) < 15:
            line = lines[i]
            url_match = re.match(r'(https?://[^\s]+)', line)
            if url_match:
                url = url_match.group(1)
                title = line[len(url):].strip()
                snippet = lines[i + 1] if i + 1 < len(lines) else ""
                if title and len(title) > 3:
                    add_result(url, title, snippet)
                i += 2
            else:
                i += 1
    
    return results

class AgentToolExecutorImpl(ToolExecutor):
    """
    Agent 工具执行器实现
    
    实现所有工具的具体执行逻辑
    
    权限检查特性：
    - 所有工具执行前进行权限检查
    - 路径边界检查（防止越权访问项目目录外文件）
    - 完整的审计日志记录
    - 统一的错误处理
    """
    
    def __init__(self, project_path: str = "E:\\agentspace"):
        self._current_project_path = project_path
        self._enforcer = PermissionEnforcer(project_path)
        self._path_whitelist: List[str] = []
        from .truncation_manager import set_truncation_dir
        set_truncation_dir(project_path)
    
    def set_project_path(self, path: str):
        """设置项目路径"""
        self._current_project_path = path
        self._enforcer.set_project_path(path)
        from .truncation_manager import set_truncation_dir
        set_truncation_dir(path)
    
    def set_path_whitelist(self, paths: List[str]) -> None:
        """设置额外白名单路径"""
        self._path_whitelist = paths
        self._enforcer.set_path_whitelist(paths)
    
    def _resolve_path(self, path: str) -> str:
        """解析路径，支持相对路径"""
        if not path:
            return path
        if os.path.isabs(path):
            print(f"[ToolExecutor] _resolve_path: {path} is absolute, returning as-is")
            return path
        resolved = os.path.join(self._current_project_path, path)
        print(f"[ToolExecutor] _resolve_path: {path} -> {resolved} (project_path={self._current_project_path})")
        return resolved
    
    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        context: ToolExecutionContext = None
    ) -> str:
        """
        执行工具
        
        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文
        
        Returns:
            执行结果字符串
        """
        # ==================== Agent 工具策略检查（enforce 白名单/黑名单） ====================
        # 即使 LLM 幻觉调用了未注入 schema 的工具，也在执行入口被拒绝
        agent_name = context.agent_name if context else ""
        policy_denied = check_agent_tool_policy(agent_name, tool_name)
        if policy_denied:
            logger.warning(f"[ToolExecutor] Agent tool policy denied for {tool_name}: {policy_denied}")
            return f"Error: {policy_denied}"

        # ==================== 权限检查（统一入口 - 独立 PermissionEnforcer） ====================
        allowed, reason, audit_info = await self._enforcer.check(tool_name, args, context)

        if not allowed:
            logger.warning(f"[ToolExecutor] Permission denied for {tool_name}: {reason}")
            return f"Error: {reason}"
        
        # ==================== 快照（写操作前备份文件） ====================
        if tool_name in ('write_file', 'edit_file', 'delete_file', 'move_file'):
            if (context and context.snapshot_session_uuid and 
                context.snapshot_message_id is not None):
                try:
                    from .snapshot.snapshot_manager import get_snapshot_manager
                    manager = get_snapshot_manager(
                        context.snapshot_session_uuid, 
                        self._current_project_path
                    )
                    if manager:
                        await manager.create_snapshot(
                            tool_name=tool_name,
                            tool_args=args,
                            message_id=context.snapshot_message_id,
                            tool_call_id=context.snapshot_tool_call_id or "unknown"
                        )
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning(f"[ToolExecutor] Snapshot creation failed for {tool_name}: {e}")
        
        # ==================== 执行工具 ====================
        try:
            # ==================== 代码执行 ====================
            if tool_name == "execute_code":
                return await self._tool_execute_code(
                    args.get("code", ""),
                    args.get("language", "python"),
                    context
                )
            
            elif tool_name == "execute_shell":
                return await self._tool_execute_shell(
                    args.get("command", ""),
                    args.get("timeout", 60),
                    context
                )
            
            # ==================== 文件操作 ====================
            elif tool_name == "read_file":
                return await self._tool_read_file(
                    args.get("file_path", ""),
                    args.get("start_line"),
                    args.get("end_line")
                )
            
            elif tool_name == "read_pdf":
                return await self._tool_read_pdf(
                    args.get("file_path", ""),
                    args.get("pages"),
                    args.get("ocr_language", "ch_sim+en")
                )
            
            elif tool_name == "write_file":
                return await self._tool_write_file(
                    args.get("file_path", ""),
                    args.get("content", ""),
                    args.get("mode", "write")
                )
            
            elif tool_name == "edit_file":
                return await self._tool_edit_file(
                    file_path=args.get("file_path", ""),
                    operation=args.get("operation", ""),
                    line_number=args.get("line_number"),
                    content=args.get("content"),
                    end_line=args.get("end_line"),
                    old_string=args.get("old_string", ""),
                    new_string=args.get("new_string", ""),
                    replace_all=args.get("replace_all", False)
                )
            
            elif tool_name == "delete_file":
                return await self._tool_delete_file(
                    args.get("path", ""),
                    args.get("recursive", False)
                )
            
            elif tool_name == "copy_file":
                return await self._tool_copy_file(
                    args.get("source", ""),
                    args.get("destination", "")
                )
            
            elif tool_name == "move_file":
                return await self._tool_move_file(
                    args.get("source", ""),
                    args.get("destination", "")
                )
            
            # ==================== 目录操作 ====================
            elif tool_name == "list_directory":
                return await self._tool_list_directory(
                    args.get("path", ""),
                    args.get("show_hidden", False),
                    args.get("recursive", False)
                )
            
            elif tool_name == "create_directory":
                return await self._tool_create_directory(args.get("path", ""))
            
            elif tool_name == "get_file_info":
                return await self._tool_get_file_info(args.get("path", ""))
            
            # ==================== 搜索 ====================
            elif tool_name == "grep":
                return await self._tool_grep(
                    pattern=args.get("pattern", ""),
                    path=args.get("path"),
                    glob_pattern=args.get("glob"),
                    output_mode=args.get("output_mode", "files_with_matches"),
                    case_insensitive=args.get("-i", False),
                    show_line_numbers=args.get("-n", True),
                    after_context=args.get("-A"),
                    before_context=args.get("-B"),
                    context=args.get("-C"),
                    file_type=args.get("type"),
                    head_limit=args.get("head_limit", 250)
                )
            
            elif tool_name == "glob":
                return await self._tool_glob(
                    pattern=args.get("pattern", ""),
                    path=args.get("path")
                )
            
            elif tool_name == "ls":
                return await self._tool_ls(
                    path=args.get("path", ""),
                    ignore=args.get("ignore", [])
                )
            
            # ==================== Git 操作 ====================
            elif tool_name == "git_status":
                return await self._tool_git_status(args.get("repo_path", "."))
            
            elif tool_name == "git_log":
                return await self._tool_git_log(
                    args.get("repo_path", "."),
                    args.get("count", 10)
                )
            
            elif tool_name == "git_diff":
                return await self._tool_git_diff(
                    args.get("repo_path", "."),
                    args.get("target")
                )
            
            # ==================== 网络操作 ====================
            elif tool_name == "fetch_url":
                return await self._tool_fetch_url(
                    args.get("url", ""),
                    args.get("timeout", 30)
                )
            
            # ==================== 系统信息 ====================
            elif tool_name == "get_env_var":
                return await self._tool_get_env_var(args.get("name", ""))
            
            elif tool_name == "get_system_info":
                return await self._tool_get_system_info()
            
            elif tool_name == "list_processes":
                return await self._tool_list_processes(args.get("filter"))
            
            # ==================== Todo 管理 ====================
            elif tool_name == "todo_write":
                return await self._tool_todo_write(args.get("todos", []))
            
            elif tool_name == "todo_read":
                return await self._tool_todo_read()
            
            # ==================== 系统内部：未知工具路由 ====================
            elif tool_name == "_unknown_tool":
                original = args.get("original_name", "")
                error = args.get("error", "未知工具")
                return f"系统路由: 工具名 '{original}' 无效。{error} 请从可用工具列表中选择一个有效的工具名称重新调用。"
            
            # ==================== 用户交互 ====================
            elif tool_name == "ask_user_question":
                return await self._tool_ask_user_question(
                    args.get("questions", []),
                    args.get("question_id")  # 使用传入的 question_id
                )
            elif tool_name == "usernote_write":
                return await self._tool_usernote_write(
                    args.get("content", "")
                )
            
            # ==================== PowerShell ====================
            elif tool_name == "powershell":
                return await self._tool_powershell(
                    args.get("command", ""),
                    args.get("timeout", 60000),
                    context
                )
            
            # ==================== 文件搜索 ====================
            elif tool_name == "search_in_file":
                return await self._tool_search_in_file(
                    args.get("file_path", ""),
                    args.get("pattern", ""),
                    args.get("ignore_case", True)
                )
            
            elif tool_name == "search_files":
                return await self._tool_search_files(
                    args.get("directory", ""),
                    args.get("pattern", ""),
                    args.get("search_type", "name"),
                    args.get("file_pattern")
                )
            
            elif tool_name == "find_files":
                return await self._tool_find_files(
                    args.get("directory", ""),
                    args.get("pattern", ""),
                    args.get("max_depth")
                )
            
            # ==================== 网络搜索 ====================
            elif tool_name in ("web_search", "search_web"):
                return await self._tool_web_search(
                    args.get("query", "")
                )
            
            # ==================== 浏览器导航 ====================
            elif tool_name == "browser_navigate":
                return await self._tool_browser_navigate(
                    args.get("url", ""),
                    args.get("extract_links", False)
                )
            
            # ==================== 浏览器下载 ====================
            elif tool_name == "browser_download":
                return await self._tool_browser_download(
                    args.get("url", ""),
                    args.get("filename", "")
                )
            
            # ==================== 进程管理 ====================
            elif tool_name == "kill_process":
                return await self._tool_kill_process(
                    args.get("pid"),
                    args.get("force", False)
                )
            
            # ==================== 环境变量 ====================
            elif tool_name == "set_env_var":
                return await self._tool_set_env_var(
                    args.get("name", ""),
                    args.get("value", "")
                )
            
            # ==================== 技能管理 ====================
            elif tool_name == "list_skills":
                return await self._tool_list_skills()
            
            elif tool_name == "load_skill":
                return await self._tool_load_skill(args.get("name", ""))
            
            # ==================== Brief 消息 ====================
            elif tool_name == "brief":
                return await self._tool_brief(
                    args.get("message", ""),
                    args.get("attachments", []),
                    args.get("status", "normal")
                )
            
            # ==================== Plan Mode ====================
            elif tool_name == "enter_plan_mode":
                return await self._tool_enter_plan_mode()
            
            elif tool_name == "exit_plan_mode":
                return await self._tool_exit_plan_mode(
                    args.get("plan_title"),
                    args.get("plan_description"),
                    args.get("steps", [])
                )
            
            elif tool_name == "create_plan":
                return await self._tool_create_plan(
                    args.get("title", ""),
                    args.get("description", ""),
                    args.get("steps", [])
                )
            
            elif tool_name == "get_plan":
                return await self._tool_get_plan()
            
            # ==================== Tool Search ====================
            elif tool_name == "tool_search":
                return await self._tool_tool_search(
                    args.get("query", ""),
                    args.get("max_results", 5)
                )
            
            elif tool_name == "list_tools":
                return await self._tool_list_tools()
            
            # ==================== MCP 工具路由 ====================
            elif is_mcp_tool(tool_name):
                return await self._execute_mcp_tool(tool_name, args, context)
            
            # ==================== LSP 代码智能（9 种操作） ====================
            elif tool_name == "lsp_go_to_definition":
                return await self._tool_lsp_go_to_definition(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            elif tool_name == "lsp_find_references":
                return await self._tool_lsp_find_references(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            elif tool_name == "lsp_hover":
                return await self._tool_lsp_hover(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            elif tool_name == "lsp_document_symbol":
                return await self._tool_lsp_document_symbol(
                    args.get("file_path", "")
                )
            
            elif tool_name == "lsp_workspace_symbol":
                return await self._tool_lsp_workspace_symbol(
                    args.get("query", "")
                )
            
            elif tool_name == "lsp_go_to_implementation":
                return await self._tool_lsp_go_to_implementation(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            elif tool_name == "lsp_prepare_call_hierarchy":
                return await self._tool_lsp_prepare_call_hierarchy(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            elif tool_name == "lsp_incoming_calls":
                return await self._tool_lsp_incoming_calls(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            elif tool_name == "lsp_outgoing_calls":
                return await self._tool_lsp_outgoing_calls(
                    args.get("file_path", ""),
                    args.get("line", 0),
                    args.get("character", 0)
                )
            
            else:
                # 尝试扩展工具
                try:
                    from multi_agents.extensions_loader import try_execute_extension
                    ext_result = try_execute_extension(tool_name, args, context)
                    if ext_result is not None:
                        return ext_result
                except ImportError:
                    pass
                except Exception as e:
                    logger.error(f"[ToolExecutor] Extension tool error for {tool_name}: {e}")
                
                return f"Error: Unknown tool '{tool_name}'"
        
        except asyncio.CancelledError:
            logger.info(f"[ToolExecutor] Cancelled while executing {tool_name}")
            raise
        
        except Exception as e:
            logger.error(f"[ToolExecutor] Error executing {tool_name}: {e}")
            return f"Error: {str(e)}"
    
    # ==================== 工具实现 ====================
    
    async def _terminate_process(self, process) -> None:
        """尽力终止进程及其子进程"""
        if process.returncode is not None:
            return

        try:
            if os.name == "nt":
                try:
                    await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=5
                    )
                except Exception:
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    return
                except Exception:
                    process.terminate()
                await asyncio.sleep(0.2)
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except Exception:
                        process.kill()
        except ProcessLookupError:
            return
        except Exception as e:
            logger.warning(f"[ToolExecutor] Failed to terminate process tree {process.pid}: {e}")
            try:
                process.kill()
            except Exception:
                pass

    async def _run_subprocess_exec(
        self,
        command: List[str],
        timeout: float,
        context: Optional[ToolExecutionContext] = None,
        cwd: Optional[str] = None
    ) -> tuple[int, str, str]:
        """以可取消的方式运行子进程"""
        popen_kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd or self._current_project_path,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        start_time = time.time()
        try:
            while True:
                if context is not None and context.cancel_event.is_set():
                    logger.info(f"[ToolExecutor] Cancelling subprocess pid={process.pid}")
                    await self._terminate_process(process)
                    try:
                        await asyncio.to_thread(process.wait, timeout=1)
                    except Exception:
                        pass
                    raise asyncio.CancelledError()

                if process.poll() is not None:
                    stdout, stderr = await asyncio.to_thread(process.communicate)
                    return (
                        process.returncode or 0,
                        stdout.decode("utf-8", errors="replace") if stdout else "",
                        stderr.decode("utf-8", errors="replace") if stderr else "",
                    )

                if time.time() - start_time > timeout:
                    await self._terminate_process(process)
                    try:
                        await asyncio.to_thread(process.wait, timeout=1)
                    except Exception:
                        pass
                    raise asyncio.TimeoutError()

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            await self._terminate_process(process)
            raise
    
    async def _tool_execute_code(
        self,
        code: str,
        language: str = "python",
        context: ToolExecutionContext = None
    ) -> str:
        """执行代码"""
        if not code:
            return "Error: No code provided"
        
        # V2 行为模式扫描：检测批量爬虫代码
        is_blocked, patterns = scan_code_for_scraping_behavior(code, language)
        if is_blocked:
            patterns_str = "; ".join(patterns)
            logger.warning(f"[ToolExecutor] Code blocked by NetworkGuardV2: {patterns_str}")
            return (
                f"⛔ [安全拦截] 代码中检测到爬虫行为模式，已被阻止执行。\n"
                f"检测到: {patterns_str}\n"
                f"说明: 禁止批量/遍历/自动化的页面访问行为。"
            )
        
        import tempfile
        
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{language}', delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            if language == "python":
                command = ['python', temp_file]
            else:
                command = [language, temp_file]

            returncode, stdout, stderr = await self._run_subprocess_exec(
                command=command,
                timeout=60,
                context=context,
                cwd=self._current_project_path
            )
            
            if returncode == 0:
                return stdout or "Success (no output)"
            return f"Error: {stderr}"

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return "Error: Execution timed out"
        except Exception as e:
            return f"Error: {str(e)}"
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception:
                    pass
    
    async def _tool_execute_shell(
        self,
        command: str,
        timeout: int = 60,
        context: ToolExecutionContext = None
    ) -> str:
        """执行Shell命令（BashValidator 全量检查）"""
        if not command:
            return "Error: No command provided"
        
        # 使用 BashValidator 全量验证
        from .bash_validator import BashValidator
        validator = BashValidator(self._current_project_path)
        validator.set_path_whitelist(getattr(self, '_path_whitelist', []))
        report = validator.validate(command)
        
        # 阻止级：拒绝执行
        if report.blocked:
            logger.warning(f"[ToolExecutor] BLOCKED command: {command} - {report.block_reason}")
            return f"Error: 命令被阻止执行 - {report.block_reason}"
        
        # 警告级：记录但允许
        if report.is_destructive:
            audit_logger = self._enforcer._get_audit_logger()
            if audit_logger:
                from gateway.permission_audit import AuditEvent, AuditEventType, AuditDecisionSource
                audit_logger.log_event(
                    AuditEvent(
                        event_type=AuditEventType.PERMISSION_CHECK.value,
                        tool_name='execute_shell',
                        target=command,
                        content=command,
                        allowed=True,
                        decision_source='dangerous_command_warning',
                        reason='; '.join(report.warnings[:3])
                    )
                )
            logger.warning(f"[ToolExecutor] Dangerous command executed: {command} - {report.warnings[:2]}")
        
        try:
            if os.name == 'nt':
                exec_command = ['cmd', '/c', command]
            else:
                exec_command = ['bash', '-c', command]

            returncode, stdout, stderr = await self._run_subprocess_exec(
                command=exec_command,
                timeout=timeout,
                context=context,
                cwd=self._current_project_path
            )

            if returncode == 0:
                return stdout or "Success (no output)"
            return f"Error: {stderr}"

        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return "Error: Command timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_read_file(self, file_path: str, start_line: int = None, end_line: int = None) -> str:
        """读取文件（包含敏感文件检查）"""
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        
        # 敏感文件检查（记录审计日志）
        is_sensitive, sensitive_reason = self._enforcer.check_sensitive_file(resolved_path)
        if is_sensitive:
            audit_logger = self._enforcer._get_audit_logger()
            if audit_logger:
                from gateway.permission_audit import AuditEvent, AuditEventType, AuditDecisionSource
                audit_logger.log_event(
                    AuditEvent(
                        event_type=AuditEventType.PERMISSION_CHECK.value,
                        tool_name='read_file',
                        target=resolved_path,
                        allowed=False,
                        decision_source=AuditDecisionSource.PATH_VIOLATION.value,
                        reason=sensitive_reason
                    )
                )
            logger.warning(f"[ToolExecutor] Sensitive file access blocked: {resolved_path} - {sensitive_reason}")
            # 注意：敏感文件检查只是警告，不阻止读取（权限管理器已经检查过）
            # 但我们记录审计日志
        
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                if start_line and end_line:
                    lines = f.readlines()
                    content = ''.join(lines[start_line-1:end_line])
                elif start_line:
                    lines = f.readlines()
                    content = ''.join(lines[start_line-1:])
                else:
                    content = f.read()
            truncated, _ = truncate_output(content)
            return truncated
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_read_pdf(self, file_path: str, pages: Optional[str] = None, ocr_language: str = "ch_sim+en") -> str:
        """
        读取 PDF 文件
        """
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        if not os.path.exists(resolved_path):
            return f"Error: File not found: {resolved_path}"
        
        # ---------- PyMuPDF 主引擎 ----------
        try:
            import fitz
            doc = fitz.open(resolved_path)
        except ImportError:
            # ---------- 降级：pdfplumber（仅数字PDF，无OCR） ----------
            try:
                import pdfplumber
                with pdfplumber.open(resolved_path) as pdf:
                    total = len(pdf.pages)
                    target_indices = self._parse_page_range(pages if pages else None, total)
                    result = []
                    for i in target_indices:
                        page = pdf.pages[i - 1]
                        text = page.extract_text() or ""
                        result.append(f"--- 第 {i} 页 ---\n\n{text}")
                    return "\n\n".join(result) if result else "(PDF 内容为空)"
            except ImportError:
                return "Error: PDF 解析库未安装（需要 PyMuPDF 或 pdfplumber）"
            except Exception as e:
                return f"Error reading PDF with pdfplumber: {e}"
        
        # ---------- PyMuPDF 正常处理 ----------
        doc_ref = doc  # 给 finally 用
        try:
            total_pages = len(doc_ref)
            target_indices = self._parse_page_range(pages if pages else None, total_pages)
            result_pages = []
            
            for page_num in target_indices:
                page = doc_ref[page_num - 1]  # PyMuPDF 0-indexed
                
                # 1) 尝试直接提取文字
                text = page.get_text().strip()
                
                if text:
                    result_pages.append(f"--- 第 {page_num} 页 ---\n\n{text}")
                else:
                    ocr_text = await self._ocr_pdf_page(page, page_num, ocr_language)
                    result_pages.append(f"--- 第 {page_num} 页（OCR识别）---\n\n{ocr_text}")
            
            return "\n\n".join(result_pages) if result_pages else "(PDF 内容为空)"
        
        except Exception as e:
            return f"Error reading PDF: {e}"
        finally:
            doc_ref.close()
    
    def _parse_page_range(self, pages: Optional[str], total: int) -> List[int]:
        if not pages or pages.strip() == "":
            return list(range(1, total + 1))
        
        pages = pages.strip()
        selected = set()
        
        try:
            for part in pages.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-", 1)
                    start, end = int(start.strip()), int(end.strip())
                    selected.update(range(max(1, start), min(total, end) + 1))
                else:
                    num = int(part)
                    if 1 <= num <= total:
                        selected.add(num)
        except ValueError:
            # 解析失败时返回全部页面
            return list(range(1, total + 1))
        
        return sorted(selected) if selected else list(range(1, total + 1))
    
    async def _ocr_pdf_page(self, page, page_num: int, ocr_language: str) -> str:
        """对 PDF 单页执行 EasyOCR（在子线程中运行）"""
        try:
            import numpy as np
            from PIL import Image
            
            # 渲染页面为图片（300 DPI）
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            img_array = np.array(img)
            
            # 延迟初始化 EasyOCR（首次调用时加载模型）
            reader = await asyncio.to_thread(self._get_easyocr_reader, ocr_language)
            
            # OCR 识别（CPU 密集型，放入线程池）
            raw_results = await asyncio.to_thread(reader.readtext, img_array)
            
            if not raw_results:
                return "(无法识别该页内容)"
            
            # 拼接识别结果（保留相对位置）
            lines = []
            for item in raw_results:
                text = item[1].strip()
                if text:
                    lines.append(text)
            return "\n".join(lines)
        
        except ImportError as e:
            return f"(OCR 引擎不可用: {e})"
        except Exception as e:
            return f"(OCR 识别失败: {e})"
    
    def _get_easyocr_reader(self, languages: str = "ch_sim+en"):
        """全局 EasyOCR Reader 延迟初始化"""
        global _easyocr_reader, _easyocr_languages
        
        lang_list = [l.strip() for l in languages.replace("+", ",").split(",") if l.strip()]
        
        # 如果已缓存相同语言的 reader，直接返回
        if _easyocr_reader is not None and _easyocr_languages == lang_list:
            return _easyocr_reader
        
        import easyocr
        _easyocr_reader = easyocr.Reader(lang_list, gpu=True)
        _easyocr_languages = lang_list
        return _easyocr_reader
    
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
            from .lsp_client import get_lsp_client, ensure_lsp_initialized
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
    
    async def _tool_write_file(self, file_path: str, content: str, mode: str = "write") -> str:
        """写入文件（包含敏感文件检查）"""
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        print(f"[ToolExecutor] write_file: input_path={file_path}, resolved_path={resolved_path}, project_path={self._current_project_path}")
        
        # 敏感文件检查
        is_sensitive, sensitive_reason = self._enforcer.check_sensitive_file(resolved_path)
        if is_sensitive:
            audit_logger = self._enforcer._get_audit_logger()
            if audit_logger:
                from gateway.permission_audit import AuditEvent, AuditEventType, AuditDecisionSource
                audit_logger.log_event(
                    AuditEvent(
                        event_type=AuditEventType.PERMISSION_CHECK.value,
                        tool_name='write_file',
                        target=resolved_path,
                        allowed=True,  # 权限已通过，但记录敏感操作
                        decision_source='sensitive_file_warning',
                        reason=sensitive_reason
                    )
                )
            logger.warning(f"[ToolExecutor] Writing to sensitive file: {resolved_path} - {sensitive_reason}")
        
        try:
            # 确保目录存在
            dir_path = os.path.dirname(resolved_path)
            print(f"[ToolExecutor] write_file: creating directory={dir_path}")
            os.makedirs(dir_path, exist_ok=True)
            
            write_mode = 'a' if mode == "append" else 'w'
            print(f"[ToolExecutor] write_file: opening file with mode={write_mode}")
            with open(resolved_path, write_mode, encoding='utf-8') as f:
                f.write(content)
            
            # 验证文件是否真的写入成功
            if os.path.exists(resolved_path):
                file_size = os.path.getsize(resolved_path)
                print(f"[ToolExecutor] write_file: SUCCESS - file exists, size={file_size} bytes, path={resolved_path}")
            else:
                print(f"[ToolExecutor] write_file: FAILED - file does not exist after write! path={resolved_path}")
                return f"Error: File was not created at {resolved_path}"
            
            result = f"Success: Written to {resolved_path}"
            lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
            return result + lsp_report
        except Exception as e:
            print(f"[ToolExecutor] write_file: Exception - {str(e)}")
            return f"Error: {str(e)}"
    
    async def _tool_edit_file(self, file_path: str, operation: str = "", line_number: int = None,
                               content: str = None, end_line: int = None,
                               old_string: str = "", new_string: str = "", 
                               replace_all: bool = False,
                               search_block: str = "", replace_block: str = "") -> str:
        """
        编辑文件
        
        支持三种模式（优先级从高到低）：
        1.【最推荐】搜索-替换块（search_block + replace_block）
           - 带上下文的搜索块，模糊唯一匹配
           - 容忍缩进/空白差异，自动处理多候选歧义
           - 类似 Claude Code/Aider/Cursor 的编辑方式
        
        2.【推荐】字符串匹配替换（old_string + new_string）
           - 在文件中精确查找 old_string 并替换为 new_string
           - 不依赖行号，不受前面编辑操作影响
           - old_string 必须唯一匹配（除非 replace_all=true）
           - old_string 必须从 read_file 返回的内容中精确复制
        
        3.【备用】行号编辑（operation + line_number）
           - 按行号进行插入、删除、替换
           - 向后兼容原有行为
        
        优先级：search_block > old_string > operation
        """
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        print(f"[ToolExecutor] edit_file: input_path={file_path}, resolved_path={resolved_path}")
        
        # 检查文件是否存在
        if not os.path.exists(resolved_path):
            return f"Error: File not found: {resolved_path}"
        
        if not os.path.isfile(resolved_path):
            return f"Error: Path is not a file: {resolved_path}"
        
        # ================================================================
        # 模式 1：搜索-替换块（最推荐，模糊匹配 + 上下文防歧义）
        # ================================================================
        if search_block:
            return await self._tool_edit_file_by_search_replace(
                resolved_path=resolved_path,
                search_block=search_block,
                replace_block=replace_block
            )
        
        # ================================================================
        # 模式 2：字符串匹配替换（兼容旧版）
        # ================================================================
        if old_string:
            return await self._tool_edit_file_by_string(
                resolved_path=resolved_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all
            )
        
        # ================================================================
        # 模式 3：行号编辑（回退，向后兼容）
        # ================================================================
        if not operation:
            return (
                "Error: 请提供 search_block（搜索-替换块，最推荐）、old_string（字符串匹配，推荐）或 operation（行号模式，备用）。\n\n"
                "搜索-替换块模式示例（最推荐）:\n"
                '  edit_file(file_path="x.py", search_block="def foo(x):\\n    return x + 1", replace_block="def foo(x):\\n    return x * 2")\n\n'
                "字符串匹配模式示例:\n"
                '  edit_file(file_path="x.py", old_string="def foo():\\n    pass", new_string="def foo():\\n    return 42")\n\n'
                "行号模式示例:\n"
                '  edit_file(file_path="x.py", operation="replace", line_number=10, content="new line")'
            )
        
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if operation == "insert":
                if line_number is None:
                    return "Error: line_number required for insert"
                if content is None:
                    return "Error: content required for insert"
                lines.insert(line_number - 1, content + '\n')
                description = f"在第 {line_number} 行插入内容"
            
            elif operation == "delete":
                if line_number is None:
                    return "Error: line_number required for delete"
                if end_line:
                    del lines[line_number - 1:end_line]
                    description = f"删除第 {line_number}-{end_line} 行"
                else:
                    deleted_content = lines[line_number - 1].rstrip('\n') if line_number - 1 < len(lines) else "(空)"
                    del lines[line_number - 1]
                    description = f"删除第 {line_number} 行（内容: {deleted_content[:80]}）"
            
            elif operation == "replace":
                if line_number is None:
                    return "Error: line_number required for replace"
                if content is None:
                    return "Error: content required for replace"
                old_content = lines[line_number - 1].rstrip('\n') if line_number - 1 < len(lines) else "(空)"
                if end_line:
                    lines[line_number - 1:end_line] = [content + '\n']
                    description = f"替换第 {line_number}-{end_line} 行"
                else:
                    lines[line_number - 1] = content + '\n'
            else:
                return f"Error: Unknown operation '{operation}'"
            
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            rel_path = os.path.relpath(resolved_path, self._current_project_path) if os.path.isabs(resolved_path) else resolved_path
            result = f"Success: {description} — {rel_path}"
            lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
            return result + lsp_report
        
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except PermissionError:
            return f"Error: Permission denied: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_edit_file_by_string(
        self, resolved_path: str, old_string: str, new_string: str, 
        replace_all: bool = False
    ) -> str:
        """
        字符串匹配替换模式（推荐）
        
        完整实现 Claude Code FileEditTool 的核心逻辑：
        1. 读取文件内容
        2. 精确查找 old_string
        3. 验证唯一性（除非 replace_all）
        4. 执行替换
        5. 返回结构化结果（含 diff 摘要）
        
        Args:
            resolved_path: 已解析的绝对文件路径
            old_string: 要替换的旧文本（精确匹配，含缩进和换行）
            new_string: 替换后的新文本
            replace_all: 是否替换所有匹配项
        
        Returns:
            执行结果字符串
        """
        # 读取文件
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except PermissionError:
            return f"Error: Permission denied: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to read file: {str(e)}"
        
        # 验证 old_string 非空
        if not old_string:
            return "Error: old_string 不能为空。请提供要替换的精确文本片段。"
        
        # 统计匹配次数
        match_count = file_content.count(old_string)
        
        # 未找到匹配
        if match_count == 0:
            # 提供帮助信息：显示文件的前 500 字符
            preview = file_content[:500]
            if len(file_content) > 500:
                preview += "\n... (文件共 {} 行，{} 字符，仅显示前 500 字符)".format(
                    file_content.count('\n') + 1, len(file_content)
                )
            
            # 尝试找到最接近的匹配行（帮助 LLM 定位错误）
            old_first_line = old_string.split('\n')[0].strip() if old_string else ""
            hint = ""
            if old_first_line:
                file_lines = file_content.split('\n')
                for i, line in enumerate(file_lines, 1):
                    if old_first_line[:20] in line:
                        hint = (
                            f"\n\n提示：文件中第 {i} 行包含相似内容，但缩进或空格可能不匹配。"
                            f"\n文件中的实际内容: {repr(line[:100])}"
                            f"\n你提供的 old_string: {repr(old_string[:100])}"
                            f"\n请从 read_file 返回的行号前缀后的内容中精确复制（包括所有缩进和空格）。"
                        )
                        break
            
            return (
                f"Error: 在文件中未找到 old_string（匹配次数: 0）。\n\n"
                f"文件路径: {resolved_path}\n"
                f"文件大小: {len(file_content)} 字符, {file_content.count(chr(10)) + 1} 行\n"
                f"搜索的文本（前 200 字符）: {repr(old_string[:200])}\n\n"
                f"文件内容预览:\n```\n{preview}\n```"
                f"{hint}"
            )
        
        # 多处匹配且未指定 replace_all
        if match_count > 1 and not replace_all:
            # 找出每个匹配位置的行号
            positions = []
            search_start = 0
            while True:
                pos = file_content.find(old_string, search_start)
                if pos == -1:
                    break
                line_num = file_content[:pos].count('\n') + 1
                # 显示匹配位置的上下文
                context_start = max(0, pos - 30)
                context_end = min(len(file_content), pos + len(old_string) + 30)
                context = file_content[context_start:context_end].replace('\n', '\\n')
                positions.append(f"  行 {line_num}: ...{repr(context[:80])}...")
                search_start = pos + 1
            
            positions_text = '\n'.join(positions[:10])
            if len(positions) > 10:
                positions_text += f"\n  ... 还有 {len(positions) - 10} 处匹配"
            
            return (
                f"Error: old_string 在文件中出现了 {match_count} 次，而不是唯一一次。\n\n"
                f"请选择以下方案之一:\n"
                f"1. 提供更多上下文行使 old_string 变得唯一（推荐）——在 old_string 前后各加 1-2 行\n"
                f"2. 设置 replace_all=true 替换所有 {match_count} 处匹配（仅适用于全局重命名）\n\n"
                f"匹配位置:\n{positions_text}"
            )
        
        # 计算 old_string 在文件中的起始行号
        first_pos = file_content.find(old_string)
        start_line = file_content[:first_pos].count('\n') + 1 if first_pos >= 0 else 1

        # 执行替换
        if replace_all:
            new_file_content = file_content.replace(old_string, new_string)
            replaced_count = match_count
        else:
            new_file_content = file_content.replace(old_string, new_string, 1)
            replaced_count = 1
        
        # 写入文件
        try:
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_file_content)
        except PermissionError:
            return f"Error: Permission denied when writing: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to write file: {str(e)}"
        
        # 构建返回信息
        try:
            rel_path = (
                os.path.relpath(resolved_path, self._current_project_path) 
                if os.path.isabs(resolved_path) else resolved_path
            )
        except ValueError:
            rel_path = os.path.basename(resolved_path)
        
        # 生成 diff 摘要
        old_preview = old_string[:80].replace('\n', '\\n')
        new_preview = new_string[:80].replace('\n', '\\n')
        if len(old_string) > 80:
            old_preview += "..."
        if len(new_string) > 80:
            new_preview += "..."
        
        if replaced_count == 1:
            result = (
                f"Success: 文件已编辑 — {rel_path}\n"
                f"  Line: {start_line}\n"
                f"  旧: {old_preview}\n"
                f"  新: {new_preview}"
            )
        else:
            result = (
                f"Success: 文件已编辑 — {rel_path}\n"
                f"  Line: {start_line}（首处）\n"
                f"  替换了 {replaced_count} 处匹配（replace_all=true）\n"
                f"  旧: {old_preview}\n"
                f"  新: {new_preview}"
            )
        lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
        return result + lsp_report
    
    # ================================================================
    # 模糊搜索-替换块编辑（新增：搜索-替换块模式）
    # ================================================================
    def _fuzzy_find_all(self, content: str, search_block: str, threshold: float = 0.85) -> list:
        """
        滑动窗口模糊匹配，返回所有候选位置及分数
        
        Args:
            content: 文件完整内容
            search_block: 搜索块文本
            threshold: 相似度阈值
        
        Returns:
            候选匹配列表，每项包含 start_line, end_line, score, matched_lines
        """
        import difflib
        
        def normalize_line(line: str) -> str:
            """归一化行：去除首尾空白，折叠连续空白"""
            return ' '.join(line.strip().split())
        
        search_lines = [normalize_line(l) for l in search_block.splitlines() if normalize_line(l)]
        if not search_lines:
            return []
        
        content_lines = content.splitlines()
        content_normalized = [normalize_line(l) for l in content_lines]
        window = len(search_lines)
        
        if window > len(content_lines):
            return []
        
        candidates = []
        for i in range(len(content_lines) - window + 1):
            window_lines = content_normalized[i:i+window]
            
            # 快速检查：首行和尾行必须高度匹配（加权）
            first_match = difflib.SequenceMatcher(None, search_lines[0], window_lines[0]).ratio()
            last_match = difflib.SequenceMatcher(None, search_lines[-1], window_lines[-1]).ratio()
            
            if first_match < 0.6 or last_match < 0.6:
                continue
            
            # 计算整体相似度
            matcher = difflib.SequenceMatcher(None, search_lines, window_lines)
            score = matcher.ratio()
            
            if score >= threshold:
                candidates.append({
                    'start_line': i + 1,
                    'end_line': i + window,
                    'score': score,
                    'matched_content': '\n'.join(content_lines[i:i+window])
                })
        
        # 按分数降序排序
        candidates.sort(key=lambda x: -x['score'])
        return candidates
    
    def _apply_search_replace(self, content: str, search_block: str, replace_block: str) -> tuple[str, str]:
        """
        应用搜索-替换块，返回 (新内容, 错误信息)
        错误信息为空表示成功
        """
        # 1. 精确匹配优先（兼容现有行为）
        if search_block in content:
            count = content.count(search_block)
            if count == 1:
                new_content = content.replace(search_block, replace_block)
                return new_content, ""
            elif count > 1:
                return content, f"Error: search_block 在文件中出现了 {count} 次，不是唯一匹配。请提供更多上下文使其唯一，或使用 replace_all=true。"
        
        # 2. 模糊匹配
        candidates = self._fuzzy_find_all(content, search_block)
        
        if not candidates:
            # 生成提示：显示文件前几行帮助定位
            preview_lines = content.splitlines()[:20]
            preview = '\n'.join(f"{i+1:4}: {line}" for i, line in enumerate(preview_lines))
            if len(content.splitlines()) > 20:
                preview += f"\n... (共 {len(content.splitlines())} 行)"
            
            return content, (
                f"Error: 未找到匹配的 search_block（模糊匹配阈值 0.85）。\n\n"
                f"文件内容预览（前 20 行）:\n```\n{preview}\n```\n\n"
                f"请确保 search_block 包含足够的上下文（建议 ≥ 3 行），并从 read_file 返回的内容中复制。"
            )
        
        if len(candidates) == 1:
            # 唯一高分匹配，直接替换
            c = candidates[0]
            content_lines = content.splitlines()
            new_lines = content_lines[:c['start_line']-1] + replace_block.splitlines() + content_lines[c['end_line']:]
            return '\n'.join(new_lines), ""
        
        # 3. 多候选：返回前 3 个让 LLM 选择
        top3 = candidates[:3]
        candidates_text = '\n'.join(
            f"  候选 {i+1} (行 {c['start_line']}-{c['end_line']}, 分数 {c['score']:.2f}):\n```\n{c['matched_content'][:200]}\n```"
            for i, c in enumerate(top3)
        )
        return content, (
            f"Error: 找到 {len(candidates)} 处相似匹配，无法自动确定唯一位置。\n\n"
            f"请在 search_block 中补充更多上下文行以消除歧义。\n\n"
            f"前 3 个候选位置:\n{candidates_text}"
        )
    
    async def _tool_edit_file_by_search_replace(
        self, resolved_path: str, search_block: str, replace_block: str
    ) -> str:
        """
        搜索-替换块编辑模式（推荐）
        
        使用带上下文的搜索块进行模糊唯一匹配，比 old_string/new_string 更健壮。
        
        Args:
            resolved_path: 已解析的绝对文件路径
            search_block: 搜索块（包含上下文的旧代码片段）
            replace_block: 替换块（新代码片段，保持相同缩进结构）
        
        Returns:
            执行结果字符串
        """
        # 读取文件
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except PermissionError:
            return f"Error: Permission denied: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to read file: {str(e)}"
        
        if not search_block:
            return "Error: search_block 不能为空。请提供要替换的代码片段（含上下文）。"
        
        # 应用搜索-替换
        new_content, error = self._apply_search_replace(file_content, search_block, replace_block)
        
        if error:
            return error
        
        # 写入文件
        try:
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except PermissionError:
            return f"Error: Permission denied when writing: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to write file: {str(e)}"
        
        # 计算变更行号（用于显示）
        import difflib
        diff = list(difflib.unified_diff(
            file_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            n=3
        ))
        
        # 构建返回信息
        try:
            rel_path = (
                os.path.relpath(resolved_path, self._current_project_path) 
                if os.path.isabs(resolved_path) else resolved_path
            )
        except ValueError:
            # 不同驱动器下 relpath 会失败，回退到文件名
            rel_path = os.path.basename(resolved_path)
        
        # 尝试找到变更的大致行号
        start_line = 1
        for line in diff:
            if line.startswith('@@'):
                import re
                m = re.search(r'@@ -(\d+)', line)
                if m:
                    start_line = int(m.group(1))
                    break
        
        old_preview = search_block[:80].replace('\n', '\\n')
        new_preview = replace_block[:80].replace('\n', '\\n')
        if len(search_block) > 80:
            old_preview += "..."
        if len(replace_block) > 80:
            new_preview += "..."
        
        result = (
            f"Success: 文件已编辑 (search/replace) — {rel_path}\n"
            f"  Line: ~{start_line}\n"
            f"  旧: {old_preview}\n"
            f"  新: {new_preview}"
        )
        lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
        return result + lsp_report
    
    async def _tool_delete_file(self, path: str, recursive: bool = False) -> str:
        """删除文件或目录"""
        if not path:
            return "Error: No path provided"
        
        import shutil
        
        resolved_path = self._resolve_path(path)
        
        try:
            if os.path.isfile(resolved_path):
                os.remove(resolved_path)
                return f"Success: File deleted: {resolved_path}"
            elif os.path.isdir(resolved_path):
                if recursive:
                    shutil.rmtree(resolved_path)
                    return f"Success: Directory deleted recursively: {resolved_path}"
                else:
                    os.rmdir(resolved_path)
                    return f"Success: Directory deleted: {resolved_path}"
            else:
                return f"Error: Path not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_copy_file(self, source: str, destination: str) -> str:
        """复制文件或目录"""
        if not source or not destination:
            return "Error: Source and destination paths required"
        
        import shutil
        
        resolved_source = self._resolve_path(source)
        resolved_destination = self._resolve_path(destination)
        
        try:
            if os.path.isfile(resolved_source):
                shutil.copy2(resolved_source, resolved_destination)
                return f"Success: File copied from {resolved_source} to {resolved_destination}"
            elif os.path.isdir(resolved_source):
                shutil.copytree(resolved_source, resolved_destination)
                return f"Success: Directory copied from {resolved_source} to {resolved_destination}"
            else:
                return f"Error: Source not found: {resolved_source}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_move_file(self, source: str, destination: str) -> str:
        """移动/重命名文件或目录"""
        if not source or not destination:
            return "Error: Source and destination paths required"
        
        import shutil
        
        resolved_source = self._resolve_path(source)
        resolved_destination = self._resolve_path(destination)
        
        try:
            shutil.move(resolved_source, resolved_destination)
            return f"Success: Moved from {resolved_source} to {resolved_destination}"
        except FileNotFoundError:
            return f"Error: Source not found: {resolved_source}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_list_directory(self, path: str, show_hidden: bool = False, recursive: bool = False) -> str:
        """列出目录内容"""
        if not path:
            return "Error: No path provided"
        
        resolved_path = self._resolve_path(path)
        
        try:
            if recursive:
                result = []
                for root, dirs, files in os.walk(resolved_path):
                    for d in dirs:
                        if not show_hidden and d.startswith('.'):
                            continue
                        result.append(f"[DIR]  {os.path.join(root, d)}")
                    for f in files:
                        if not show_hidden and f.startswith('.'):
                            continue
                        full_path = os.path.join(root, f)
                        size = os.path.getsize(full_path)
                        result.append(f"[FILE] {full_path} ({size} bytes)")
            else:
                entries = os.listdir(resolved_path)
                result = []
                for entry in entries:
                    if not show_hidden and entry.startswith('.'):
                        continue
                    full_path = os.path.join(resolved_path, entry)
                    if os.path.isdir(full_path):
                        result.append(f"[DIR]  {entry}")
                    else:
                        size = os.path.getsize(full_path)
                        result.append(f"[FILE] {entry} ({size} bytes)")
            return "\n".join(result)
        except FileNotFoundError:
            return f"Error: Directory not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_create_directory(self, path: str) -> str:
        """创建目录"""
        if not path:
            return "Error: No path provided"
        
        resolved_path = self._resolve_path(path)
        
        try:
            os.makedirs(resolved_path, exist_ok=True)
            return f"Success: Directory created: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_get_file_info(self, path: str) -> str:
        """获取文件信息"""
        if not path:
            return "Error: No path provided"
        
        resolved_path = self._resolve_path(path)
        
        try:
            stat = os.stat(resolved_path)
            return json.dumps({
                "path": resolved_path,
                "size": stat.st_size,
                "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime)),
                "created": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_ctime)),
                "is_directory": os.path.isdir(resolved_path)
            }, indent=2)
        except FileNotFoundError:
            return f"Error: Path not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_grep(self, pattern: str, path: str = None, glob_pattern: str = None,
                          output_mode: str = "files_with_matches", case_insensitive: bool = False,
                          show_line_numbers: bool = True, after_context: int = None,
                          before_context: int = None, context: int = None, file_type: str = None,
                          head_limit: int = 250) -> str:
        """使用 ripgrep 进行文件内容搜索"""
        if not pattern:
            return "Error: No pattern provided"
        
        search_path = self._resolve_path(path) if path else self._current_project_path
        
        cmd = ["rg"]
        
        if output_mode == "content":
            if show_line_numbers:
                cmd.append("-n")
            if after_context:
                cmd.extend(["-A", str(after_context)])
            if before_context:
                cmd.extend(["-B", str(before_context)])
            if context:
                cmd.extend(["-C", str(context)])
        elif output_mode == "count":
            cmd.append("-c")
        else:
            cmd.append("-l")
        
        if case_insensitive:
            cmd.append("-i")
        
        for vcs_dir in ['.git', '.svn', '.hg']:
            cmd.extend(["--glob", f"!{vcs_dir}"])
        
        if glob_pattern:
            cmd.extend(["--glob", glob_pattern])
        
        if file_type:
            cmd.extend(["--type", file_type])
        
        cmd.append("--")
        cmd.append(pattern)
        cmd.append(search_path)
        
        try:
            # rg 输出恒为 UTF-8；显式 encoding 防止中文 Windows 上 GBK 解码失败导致
            # stdout=None 被静默丢弃（表现为"文件明明有却返回 No matches found"）
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                    encoding='utf-8', errors='replace',
                                    stdin=subprocess.DEVNULL)
            
            if result.returncode not in [0, 1]:
                return f"Error: {result.stderr}" if result.stderr else "Search failed"
            
            output = (result.stdout or '').strip()
            
            if not output:
                return "No matches found"
            
            if output_mode == "files_with_matches":
                lines = output.split('\n')
                all_files = [line for line in lines if line.strip()]
                sliced_files = all_files[:head_limit] if head_limit > 0 else all_files
                return json.dumps({
                    "mode": output_mode,
                    "numFiles": len(sliced_files),
                    "filenames": sliced_files
                }, ensure_ascii=False, indent=2)
            
            elif output_mode == "count":
                lines = output.split('\n')
                counts = []
                for line in lines:
                    if ':' in line:
                        filepath, count = line.rsplit(':', 1)
                        counts.append({"file": filepath, "count": int(count.strip())})
                return json.dumps({
                    "mode": output_mode,
                    "numFiles": len(counts),
                    "counts": counts[:head_limit] if head_limit > 0 else counts
                }, ensure_ascii=False, indent=2)
            
            else:
                lines = output.split('\n')
                sliced_lines = lines[:head_limit] if head_limit > 0 else lines
                return json.dumps({
                    "mode": output_mode,
                    "numLines": len(sliced_lines),
                    "content": '\n'.join(sliced_lines)
                }, ensure_ascii=False, indent=2)
        
        except subprocess.TimeoutExpired:
            return "Error: Search timed out"
        except FileNotFoundError:
            return "Error: ripgrep (rg) not found"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_glob(self, pattern: str, path: str = None) -> str:
        """使用 glob 模式匹配查找文件"""
        if not pattern:
            return "Error: No pattern provided"
        
        import fnmatch
        
        search_path = self._resolve_path(path) if path else self._current_project_path
        
        if not os.path.exists(search_path):
            return f"Error: Directory does not exist: {search_path}"
        
        matches = []
        
        try:
            for root, dirs, files in os.walk(search_path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in 
                          ['node_modules', '__pycache__', '.git', 'venv', 'dist', 'build']]
                
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern.replace('**/', '').replace('/**', '')):
                        matches.append(os.path.join(root, filename))
            
            matches.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
            
            max_results = 100
            truncated = len(matches) > max_results
            if truncated:
                matches = matches[:max_results]
            
            return json.dumps({
                "numFiles": len(matches),
                "filenames": matches,
                "truncated": truncated
            }, ensure_ascii=False, indent=2)
        
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_ls(self, path: str, ignore: List[str] = None) -> str:
        """列出目录内容"""
        if not path:
            return "Error: No path provided"
        
        import fnmatch
        
        resolved_path = self._resolve_path(path)
        
        if not os.path.exists(resolved_path):
            return f"Error: Path does not exist: {resolved_path}"
        
        if not os.path.isdir(resolved_path):
            return f"Error: Path is not a directory: {resolved_path}"
        
        ignore_patterns = ignore or []
        
        try:
            entries = []
            
            for entry in os.listdir(resolved_path):
                should_ignore = False
                for pattern in ignore_patterns:
                    if fnmatch.fnmatch(entry, pattern):
                        should_ignore = True
                        break
                
                if not should_ignore:
                    full_path = os.path.join(resolved_path, entry)
                    is_dir = os.path.isdir(full_path)
                    entries.append({
                        "name": entry,
                        "type": "directory" if is_dir else "file",
                        "path": full_path
                    })
            
            entries.sort(key=lambda x: (x["type"] == "file", x["name"].lower()))
            
            return json.dumps({
                "path": resolved_path,
                "entries": entries,
                "count": len(entries)
            }, ensure_ascii=False, indent=2)
        
        except PermissionError:
            return "Error: Permission denied"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_git_status(self, repo_path: str = ".") -> str:
        """获取Git状态"""
        try:
            result = subprocess.run(
                ['git', 'status'],
                cwd=self._resolve_path(repo_path),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Git status timed out"
        except FileNotFoundError:
            return "Error: Git not found"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_git_log(self, repo_path: str = ".", count: int = 10) -> str:
        """获取Git日志"""
        try:
            result = subprocess.run(
                ['git', 'log', '--oneline', f'-{count}'],
                cwd=self._resolve_path(repo_path),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Git log timed out"
        except FileNotFoundError:
            return "Error: Git not found"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_git_diff(self, repo_path: str = ".", target: str = None) -> str:
        """获取Git差异"""
        try:
            cmd = ['git', 'diff']
            if target:
                cmd.append(target)
            
            result = subprocess.run(
                cmd,
                cwd=self._resolve_path(repo_path),
                capture_output=True,
                text=True,
                encoding='utf-8', errors='replace',
                stdin=subprocess.DEVNULL,
                timeout=30
            )
            
            if result.returncode == 0:
                return result.stdout or "No differences"
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Git diff timed out"
        except FileNotFoundError:
            return "Error: Git not found"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_fetch_url(self, url: str, timeout: int = 30) -> str:
        """获取网页内容"""
        if not url:
            return "Error: No URL provided"
        
        # 第一阶段：httpx 快速获取
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                raw_text = resp.text
                content_type = resp.headers.get("content-type", "")
        except Exception as e:
            return f"Error: Failed to fetch URL: {e}"
        
        # 检测是否为 JS 渲染的 SPA / 反爬页面（内容稀疏或无实质文本）
        import re
        text_only = re.sub(r'<[^>]+>', '', raw_text).strip()
        html_size = len(raw_text)
        text_size = len(text_only)
        # 覆盖：SPA、Cloudflare 验证页、年龄验证墙、动态加载页面等
        is_sparse = text_size < 500 and html_size > 20000
        # 特定已知反爬模式（快速命中，不依赖启发判断）
        anti_bot = bool(re.search(
            r'(challenge-running|captcha|cf-browser-verification|'
            r'age-verify|age_gate|__NEXT_DATA__|window\.__NUXT__)',
            raw_text, re.IGNORECASE
        ))
        
        if (is_sparse or anti_bot) and is_bridge_available():
            # 降级到浏览器：JS 渲染的 SPA，httpx 拿不到实质内容
            logger.info(f"[ToolExecutor] fetch_url detected SPA page, falling back to browser: {url}")
            bridge = get_bridge()
            try:
                tab = await bridge.create_tab(url)
                tab_id = tab.get("tab", {}).get("id")
                if tab_id:
                    await bridge.navigate(tab_id, url, timeout=min(timeout, 30))
                    content = await bridge.get_page_content(tab_id)
                    await bridge.close_tab(tab_id)
                    if content and content.get("text"):
                        full_text = content["text"]
                        truncated, was_truncated = truncate_output(full_text)
                        output_parts = [
                            f"## {content.get('title', '')}",
                            f"URL: {url}",
                            f"",
                            f"### Page Content (rendered by browser):",
                            truncated
                        ]
                        return "\n".join(output_parts)
            except Exception as e:
                logger.warning(f"[ToolExecutor] Browser fallback for fetch_url failed: {e}")
            
            return truncate_output(raw_text)[0]
        
        return truncate_output(raw_text)[0]
    
    async def _tool_get_env_var(self, name: str) -> str:
        """获取环境变量"""
        if not name:
            return "Error: Variable name required"
        
        value = os.environ.get(name)
        if value is not None:
            return f"{name}={value}"
        else:
            return f"Error: Environment variable '{name}' not found"
    
    async def _tool_get_system_info(self) -> str:
        """获取系统信息"""
        import platform
        
        try:
            info = {
                "system": platform.system(),
                "node": platform.node(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "cwd": os.getcwd(),
                "project_path": self._current_project_path,
            }
            
            try:
                import psutil
                info["cpu_count"] = psutil.cpu_count()
                mem = psutil.virtual_memory()
                info["memory_total"] = f"{mem.total // (1024**3)} GB"
                info["memory_available"] = f"{mem.available // (1024**3)} GB"
            except ImportError:
                pass
            
            return json.dumps(info, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_list_processes(self, filter_name: str = None) -> str:
        """列出进程"""
        try:
            if os.name == 'nt':
                result = subprocess.run(
                    ['tasklist'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            else:
                result = subprocess.run(
                    ['ps', 'aux'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if filter_name:
                    lines = [l for l in lines if filter_name.lower() in l.lower()]
                return '\n'.join(lines[:50])
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Process list timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_todo_write(self, todos: list) -> str:
        """更新任务列表"""
        if not todos:
            return "Error: todos 参数为空"
        
        try:
            from tools.todo_tool import TodoManager
            
            processed_todos = []
            for i, item in enumerate(todos):
                if not isinstance(item, dict):
                    return f"Error: todo item {i} 必须是字典"
                if "content" not in item:
                    return f"Error: todo item {i} 必须有 'content' 字段"
                
                if "id" not in item:
                    item["id"] = f"todo_{uuid.uuid4().hex[:8]}"
                
                status = item.get("status", "pending")
                if status not in ["pending", "in_progress", "completed"]:
                    return f"Error: todo item {i} 状态无效: {status}"
                
                processed_todos.append(item)
            
            old_todos, new_todos = TodoManager.update_todos(processed_todos)
            
            return json.dumps({
                "old_count": len(old_todos),
                "new_count": len(new_todos),
                "todos": [t.to_dict() for t in new_todos]
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: TodoManager not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_todo_read(self) -> str:
        """读取任务列表"""
        try:
            from tools.todo_tool import TodoManager, TodoStatus
            
            todos = TodoManager.get_todos()
            
            return json.dumps({
                "todos": [t.to_dict() for t in todos],
                "count": len(todos),
                "pending": sum(1 for t in todos if t.status == TodoStatus.PENDING),
                "in_progress": sum(1 for t in todos if t.status == TodoStatus.IN_PROGRESS),
                "completed": sum(1 for t in todos if t.status == TodoStatus.COMPLETED)
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: TodoManager not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_ask_user_question(self, questions: List[Dict[str, Any]], question_id: str = None) -> str:
        """向用户提出问题"""
        if not questions:
            return "Error: No questions provided"
        
        if len(questions) < 1 or len(questions) > 4:
            return "Error: Questions must have 1-4 items"
        
        # 验证问题格式
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                return f"Error: Question {i} must be a dictionary"
            if "question" not in q:
                return f"Error: Question {i} must have 'question' field"
            if "header" not in q:
                return f"Error: Question {i} must have 'header' field"
            if "options" not in q:
                return f"Error: Question {i} must have 'options' field"
        
        try:
            from tools.ask_question_tool import QuestionManager
            
            # 注意：set_pending_questions 已由 agent_chat_service_v2.py 在 yield ask_question
            # 事件之前执行，此处不再重复调用，避免清掉用户已提交的答案。
            
            # 等待用户回答（阻塞直到前端提交答案）
            answers = await asyncio.wait_for(
                QuestionManager.wait_for_answers(),
                timeout=300.0
            )
            
            if answers is None:
                return json.dumps({
                    "cancelled": True,
                    "message": "Question was cancelled"
                }, ensure_ascii=False)
            
            return json.dumps({
                "questions": questions,
                "answers": answers.get("answers", {}),
                "cancelled": False
            }, ensure_ascii=False, indent=2)
        
        except asyncio.TimeoutError:
            logger.warning("[ToolExecutor] ask_user_question timed out")
            return json.dumps({
                "cancelled": True,
                "message": "Question timed out"
            }, ensure_ascii=False)
        except ImportError:
            return "Error: QuestionManager not available"
        except Exception as e:
            logger.error(f"[ToolExecutor] ask_user_question error: {e}")
            return f"Error: {str(e)}"
    
    async def _tool_usernote_write(self, content: str) -> str:
        """写入用户便签"""
        try:
            import os as _uos
            _data_home = _uos.environ.get("PEBBLE_HOME") or _uos.path.join(_uos.path.expanduser("~"), ".pebble")
            _uos.makedirs(_data_home, exist_ok=True)
            _un_path = _uos.path.join(_data_home, "usernote.json")
            with open(_un_path, "w", encoding="utf-8") as _f:
                from datetime import datetime
                json.dump({"content": content, "updated_at": datetime.now().isoformat()}, _f, ensure_ascii=False)
            return f"usernote 已更新 ({len(content)} 字符)"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_powershell(
        self,
        command: str,
        timeout: int = 60000,
        context: ToolExecutionContext = None
    ) -> str:
        """执行 PowerShell 命令"""
        if not command:
            return "Error: No command provided"
        
        # 检测 PowerShell 版本
        ps_exe = "pwsh" if self._has_pwsh() else "powershell"
        
        ps_command = [
            ps_exe,
            "-NoLogo",
            "-NonInteractive",
            "-NoProfile",
            "-Command",
            command
        ]
        
        timeout_sec = timeout / 1000
        
        try:
            returncode, stdout_text, stderr_text = await self._run_subprocess_exec(
                command=ps_command,
                timeout=timeout_sec,
                context=context,
                cwd=self._current_project_path
            )

            output = ""
            if stdout_text:
                output += stdout_text
            if stderr_text:
                output += f"\n[Error]: {stderr_text}"
            
            if returncode != 0:
                output += f"\n[Exit Code]: {returncode}"
            
            return output.strip() or "Command completed with no output"
        
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return f"Error: Command timed out after {timeout_sec} seconds"
        except FileNotFoundError:
            return "Error: PowerShell not found"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def _has_pwsh(self) -> bool:
        """检查是否安装了 PowerShell 7+"""
        try:
            result = subprocess.run(
                ["pwsh", "-Command", "$PSVersionTable.PSVersion"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False
    
    # ==================== 文件搜索工具 ====================
    
    async def _tool_search_in_file(self, file_path: str, pattern: str, ignore_case: bool = True) -> str:
        """在文件中搜索内容"""
        if not file_path or not pattern:
            return "Error: file_path and pattern required"
        
        import re
        
        resolved_path = self._resolve_path(file_path)
        
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
            
            with open(resolved_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            results = []
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    results.append(f"Line {i}: {line.rstrip()}")
            
            if results:
                return f"Found {len(results)} matches:\n" + "\n".join(results[:50])
            else:
                return "No matches found"
        
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_search_files(self, directory: str, pattern: str, search_type: str = "name",
                                  file_pattern: str = None,
                                  _max_seconds: float = _SEARCH_TIME_BUDGET,
                                  _max_results: int = _SEARCH_MAX_RESULTS) -> str:
        """
        在目录中搜索文件名或内容（加固版）。

        加固点：
        - asyncio.to_thread 执行，不阻塞事件循环（外部 wait_for 兜底防单次 IO 卡死）
        - 目录剪枝（隐藏目录 + 依赖/构建/缓存目录），显式指定起点目录不受剪枝影响
        - content/both 模式：流式分块搜索（内存恒定），扩展名黑名单 + NUL 内容检测过滤二进制
        - 时间预算（每块检查）与结果上限（达到即早退）
        """
        if not directory or not pattern:
            return "Error: directory and pattern required"

        resolved_dir = self._resolve_path(directory)

        if not os.path.isdir(resolved_dir):
            return f"Error: Directory not found: {resolved_dir}"

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._search_files_sync,
                    resolved_dir, pattern, search_type, file_pattern,
                    _max_seconds, _max_results,
                ),
                timeout=_SEARCH_OUTER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return (f"Error: search timed out after {_SEARCH_OUTER_TIMEOUT:.0f}s "
                    f"(directory: {resolved_dir}). Try a narrower directory or use grep.")
        except Exception as e:
            return f"Error: {str(e)}"

    def _search_files_sync(self, resolved_dir: str, pattern: str, search_type: str,
                           file_pattern: str, max_seconds: float, max_results: int) -> str:
        """search_files 同步实现（在线程池中运行，不阻塞事件循环）"""
        import fnmatch
        import re

        start_time = time.monotonic()
        deadline = start_time + max_seconds
        results: List[str] = []
        skipped_binary = 0
        limit_reached = False
        timed_out = False

        regex = None
        if search_type in ("content", "both"):
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        try:
            for root, dirs, files in os.walk(resolved_dir):
                # 目录剪枝：隐藏目录 + 依赖/构建/缓存目录（仅剪子目录，起点目录不受影响）
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d.lower() not in _SEARCH_SKIP_DIRS
                ]

                for filename in files:
                    # 时间预算检查（每文件检查点）
                    if time.monotonic() > deadline:
                        timed_out = True
                        break

                    # 文件过滤
                    if file_pattern and not fnmatch.fnmatch(filename, file_pattern):
                        continue

                    filepath = os.path.join(root, filename)
                    match = False

                    if search_type in ("name", "both"):
                        if pattern.lower() in filename.lower():
                            match = True

                    if search_type in ("content", "both") and not match:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in _SEARCH_SKIP_EXTS:
                            skipped_binary += 1
                        else:
                            is_match, is_binary, f_timeout = _stream_search(
                                filepath, regex, pattern, deadline
                            )
                            if f_timeout:
                                timed_out = True
                                break
                            if is_binary:
                                skipped_binary += 1
                            elif is_match:
                                match = True

                    if match:
                        results.append(filepath)
                        if len(results) >= max_results:
                            limit_reached = True
                            break

                if timed_out or limit_reached:
                    break

        except Exception as e:
            return f"Error: {str(e)}"

        notes = []
        if timed_out:
            notes.append(f"timed out after {max_seconds:.0f}s, partial results")
        if limit_reached:
            notes.append(f"result limit reached ({max_results}), search stopped early")
        if skipped_binary:
            notes.append(f"{skipped_binary} binary file(s) skipped")

        if results:
            output = f"Found {len(results)} files:\n" + "\n".join(results)
        else:
            output = "No files found"
        if notes:
            output += "\nNote: " + "; ".join(notes)
        return output
    
    async def _tool_find_files(self, directory: str, pattern: str, max_depth: int = None) -> str:
        """查找文件"""
        if not directory or not pattern:
            return "Error: directory and pattern required"
        
        import fnmatch
        
        resolved_dir = self._resolve_path(directory)
        
        try:
            results = []
            base_depth = resolved_dir.count(os.sep)
            
            for root, dirs, files in os.walk(resolved_dir):
                # 深度检查
                if max_depth is not None:
                    current_depth = root.count(os.sep) - base_depth
                    if current_depth > max_depth:
                        continue
                
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern):
                        results.append(os.path.join(root, filename))
            
            if results:
                return f"Found {len(results)} files:\n" + "\n".join(results[:50])
            else:
                return "No files found"
        
        except FileNotFoundError:
            return f"Error: Directory not found: {resolved_dir}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    # ==================== 网络搜索工具 ====================
    
    async def _tool_web_search(self, query: str) -> str:
        """
        使用 Electron 内置浏览器搜索网络
        
        通过 Electron HTTP 桥接直接控制浏览器：
        1. 创建临时标签页
        2. 导航到搜索引擎（Bing）
        3. 提取搜索结果
        4. 关闭临时标签页
        
        受 code_network_guardV2 的速率限制保护。
        """
        if not query:
            return "Error: No query provided"
        
        # 速率限制检查
        rate_limiter = get_rate_limiter()
        allowed, msg = rate_limiter.check(query)
        if not allowed:
            return f"Error: {msg}"
        
        # 检查桥接可用性
        bridge = get_bridge()
        bridge_ok = is_bridge_available()
        
        try:
            if bridge_ok:
                # 首选：通过 Electron 浏览器搜索（可获得完整 JS 渲染结果）
                result = await bridge.search(query, engine="bing", timeout=15)
            else:
                # 降级：直接通过 httpx 抓取 Bing 搜索页（Electron 未启动时的后备方案）
                logger.info(f"[ToolExecutor] Bridge unavailable, falling back to httpx search for: {query}")
                from urllib.parse import quote
                search_url = f"https://cn.bing.com/search?q={quote(query)}&count=8"
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(
                        search_url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    )
                # 解析 Bing 搜索结果 HTML，提取结构化结果
                parsed_results = _parse_bing_search_results(resp.text)
                result = {
                    "content": {
                        "text": resp.text,
                        "url": str(resp.url),
                        "title": "",
                        "links": [
                            {"href": r["url"], "text": r["title"]}
                            for r in parsed_results
                        ],
                        "_parsed_results": parsed_results,
                    }
                }
            
            content = result.get("content")
            if not content:
                return f"Web search for: {query}\nNo results returned from browser."
            
            # 提取搜索结果文本
            text = content.get("text", "")
            links = content.get("links", [])
            url = content.get("url", "")
            
            # 优先使用已解析的结构化结果（httpx 路径有 _parsed_results，桥接路径从 links 提取）
            parsed_results = content.get("_parsed_results", [])
            if not parsed_results and links:
                for link in links:
                    href = link.get("href", "")
                    if href and not any(ignore in href for ignore in ["bing.com", "microsoft.com", "#", "javascript:"]):
                        parsed_results.append({
                            "title": link.get("text", "").strip(),
                            "url": href,
                            "snippet": "",
                        })
            
            # 格式化输出
            output_parts = [f"## Search Results: {query}", f"Search page: {url}\n"]
            
            if parsed_results:
                output_parts.append(f"Found {len(parsed_results)} results:")
                for i, r in enumerate(parsed_results[:15], 1):
                    title = r.get("title", "").strip()
                    snippet = r.get("snippet", "").strip()
                    result_url = r.get("url", "")
                    output_parts.append(f"")
                    output_parts.append(f"{i}. **{title}**")
                    if snippet:
                        output_parts.append(f"   {snippet}")
                    output_parts.append(f"   {result_url}")
                output_parts.append("")
            
            # 附上原始文本（限长，无结构化结果时的备用）
            if text and not parsed_results:
                truncated, _ = truncate_output(text)
                output_parts.append(f"### Page Content (raw):\n{truncated}")
            
            return "\n".join(output_parts)
            
        except asyncio.CancelledError:
            raise
        except ElectronBridgeError as e:
            logger.error(f"[ToolExecutor] Electron bridge search error: {e}")
            return f"Error: Browser search failed - {str(e)}"
        except Exception as e:
            logger.error(f"[ToolExecutor] Unexpected search error: {e}")
            return f"Error: Search failed - {str(e)}"
    
    async def _tool_browser_navigate(self, url: str, extract_links: bool = False) -> str:
        """
        导航到指定 URL 并获取页面内容
        
        受 path_depth_guard 限制器保护（翻页检测 + 每轮次数限制）。
        """
        if not url:
            return "Error: No URL provided"
        
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
        
        # 路径深度检查（翻页检测 + 每轮导航次数）
        path_guard = get_path_depth_guard()
        allowed, msg = path_guard.check_navigate(url)
        if not allowed:
            return f"Error: {msg}"
        
        # 速率限制
        rate_limiter = get_rate_limiter()
        allowed, msg = rate_limiter.check(url)
        if not allowed:
            return f"Error: {msg}"
        
        bridge = get_bridge()
        bridge_ok = is_bridge_available()
        
        if not bridge_ok:
            # 降级：直接用 httpx 获取页面内容
            logger.info(f"[ToolExecutor] Bridge unavailable, falling back to httpx for navigate: {url}")
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                    )
                raw = resp.text
                import re
                text_only = re.sub(r'<[^>]+>', '\n', raw)
                lines = [l.strip() for l in text_only.split('\n') if l.strip()]
                clean_text = '\n'.join(lines[:500])
                truncated, _ = truncate_output(clean_text)
                output_parts = [
                    f"## URL: {url}",
                    f"(Fetched via httpx — Electron bridge not available)",
                    f"",
                    f"### Page Content:",
                    truncated
                ]
                return "\n".join(output_parts)
            except Exception as e:
                return f"Error: Failed to fetch URL via httpx: {e}"
        
        try:
            # 创建新标签页
            tab_result = await bridge.create_tab(url)
            if not tab_result.get("tab"):
                return "Error: Failed to create browser tab"
            
            tab_id = tab_result["tab"]["id"]
            
            # 导航到 URL（等待加载）
            await bridge.navigate(tab_id, url, timeout=30)
            
            # 获取页面内容
            content = await bridge.get_page_content(tab_id)
            
            # 关闭标签页
            await bridge.close_tab(tab_id)
            
            if not content:
                return f"Navigated to {url} but could not extract content."
            
            text = content.get("text", "")
            title = content.get("title", "")
            metadata = content.get("metadata", {})
            
            output_parts = [
                f"## Page: {title}",
                f"URL: {url}",
                f"Description: {metadata.get('description', 'N/A')}",
                f""
            ]
            
            if extract_links:
                links = content.get("links", [])
                if links:
                    output_parts.append(f"### Links ({len(links)} total, showing first 20):")
                    for i, link in enumerate(links[:20], 1):
                        link_text = link.get("text", "").strip()
                        link_href = link.get("href", "")
                        if link_text:
                            output_parts.append(f"{i}. [{link_text}]({link_href})")
                        else:
                            output_parts.append(f"{i}. {link_href}")
                    output_parts.append("")
            
            if text:
                truncated, _ = truncate_output(text)
                output_parts.append(f"### Page Content:\n{truncated}")
            
            return "\n".join(output_parts)
            
        except asyncio.CancelledError:
            raise
        except ElectronBridgeError as e:
            logger.error(f"[ToolExecutor] Browser navigate error: {e}")
            return f"Error: Navigation failed - {str(e)}"
        except Exception as e:
            logger.error(f"[ToolExecutor] Unexpected navigate error: {e}")
            return f"Error: Navigation failed - {str(e)}"
    
    async def _tool_browser_download(self, url: str, filename: str = "") -> str:
        """
        通过浏览器下载文件
        
        文件直接保存到 agent 工作区文件夹，跳过浏览器保存对话框。
        受 download_guard 限制器保护（每轮文件数 + 文件类型 + 大小）。
        """
        if not url:
            return "Error: No URL provided"
        
        # 下载限制检查
        download_guard = get_download_guard()
        allowed, msg = download_guard.check_download(url, filename)
        if not allowed:
            return f"Error: {msg}"
        
        bridge = get_bridge()
        bridge_ok = is_bridge_available()
        
        # 解析保存路径：工作区文件夹
        from urllib.parse import urlparse, unquote
        if not filename:
            parsed = urlparse(url)
            path_part = unquote(parsed.path.split('/')[-1] or 'download')
            filename = path_part if path_part else 'download'
        
        save_path = os.path.join(self._current_project_path, filename)
        
        if not bridge_ok:
            # 降级：直接用 httpx 下载文件
            logger.info(f"[ToolExecutor] Bridge unavailable, falling back to httpx download: {url}")
            try:
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    resp = await client.get(url)
                # 根据 Content-Type 推断扩展名
                content_type = resp.headers.get("content-type", "")
                ext_map = {
                    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                    "image/webp": ".webp", "image/svg+xml": ".svg",
                    "application/pdf": ".pdf", "application/zip": ".zip",
                    "text/html": ".html", "text/plain": ".txt",
                    "application/json": ".json",
                }
                if not os.path.splitext(filename)[1]:
                    for mime, ext in ext_map.items():
                        if mime in content_type:
                            filename += ext
                            break
                    else:
                        filename += ".bin"
                    save_path = os.path.join(self._current_project_path, filename)
                os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                with open(save_path, 'wb') as f:
                    f.write(resp.content)
                file_size = len(resp.content)
                return (
                    f"Download completed (via httpx — Electron bridge not available).\n"
                    f"File: {filename}\n"
                    f"URL: {url}\n"
                    f"Save path: {save_path}\n"
                    f"Size: {file_size} bytes\n"
                    f"Status: completed"
                )
            except Exception as e:
                return f"Error: Download failed via httpx: {e}"
        
        try:
            result = await bridge.trigger_download(
                url, 
                filename=filename,
                save_path=save_path
            )
            
            if result.get("success"):
                return (
                    f"Download started successfully.\n"
                    f"File: {filename}\n"
                    f"URL: {url}\n"
                    f"Save path: {save_path}\n"
                    f"Status: downloading\n"
                    f"\nFile is being saved to your workspace folder."
                )
            else:
                return f"Error: Failed to start download - {result.get('error', 'Unknown error')}"
                
        except asyncio.CancelledError:
            raise
        except ElectronBridgeError as e:
            logger.error(f"[ToolExecutor] Browser download error: {e}")
            return f"Error: Download failed - {str(e)}"
        except Exception as e:
            logger.error(f"[ToolExecutor] Unexpected download error: {e}")
            return f"Error: Download failed - {str(e)}"
    
    # ==================== 进程管理工具 ====================
    
    async def _tool_kill_process(self, pid: int, force: bool = False) -> str:
        """终止进程"""
        if pid is None:
            return "Error: PID required"
        
        import signal
        
        try:
            if os.name == 'nt':
                # Windows
                cmd = ['taskkill', '/F' if force else '', '/PID', str(pid)]
                cmd = [c for c in cmd if c]  # 移除空字符串
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            else:
                # Linux/Mac
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.kill(pid, sig)
                result = type('obj', (object,), {'returncode': 0, 'stdout': 'Process killed'})()
            
            if result.returncode == 0:
                return f"Success: Process {pid} terminated"
            else:
                return f"Error: {result.stderr}"
        except ProcessLookupError:
            return f"Error: Process {pid} not found"
        except subprocess.TimeoutExpired:
            return "Error: Kill command timed out"
        except Exception as e:
            return f"Error: {str(e)}"
    
    # ==================== 环境变量工具 ====================
    
    async def _tool_set_env_var(self, name: str, value: str) -> str:
        """设置环境变量"""
        if not name:
            return "Error: Variable name required"
        
        try:
            os.environ[name] = value
            return f"Success: Set {name}={value}"
        except Exception as e:
            return f"Error: {str(e)}"
    
    # ==================== 技能管理工具 ====================
    
    async def _tool_list_skills(self) -> str:
        """列出所有可用的技能"""
        try:
            from skills_loader import list_skills
            skills = list_skills()
            
            if not skills:
                return "当前没有可用的技能。"
            
            result = "可用技能列表：\n\n"
            for skill in skills:
                result += f"- **{skill['name']}**: {skill['description']}\n"
            
            result += "\n使用 load_skill(name) 加载特定技能的完整内容。"
            return result
        except ImportError:
            return "Error: skills_loader not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_load_skill(self, name: str) -> str:
        """加载指定技能的完整内容"""
        if not name:
            return "Error: 请指定技能名称"
        
        try:
            from skills_loader import load_skill
            content = load_skill(name)
            
            if content:
                return f"=== 技能: {name} ===\n\n{content}"
            else:
                return f"未找到技能 '{name}'。使用 list_skills() 查看可用技能。"
        except ImportError:
            return "Error: skills_loader not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    # ==================== Brief 消息工具 ====================
    
    async def _tool_brief(self, message: str, attachments: List[str] = None, 
                          status: str = "normal") -> str:
        """发送简短消息给用户"""
        if not message:
            return "Error: No message provided"
        
        if status not in ['normal', 'proactive']:
            return "Error: status must be 'normal' or 'proactive'"
        
        # 处理附件
        resolved_attachments = []
        if attachments:
            for path in attachments:
                resolved_path = self._resolve_path(path)
                if os.path.exists(resolved_path):
                    stat = os.stat(resolved_path)
                    is_image = os.path.splitext(path)[1].lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'}
                    resolved_attachments.append({
                        'path': resolved_path,
                        'size': stat.st_size,
                        'is_image': is_image
                    })
        
        from datetime import datetime
        result = {
            'message': message,
            'attachments': resolved_attachments,
            'status': status,
            'sent_at': datetime.now().isoformat()
        }
        
        n = len(resolved_attachments)
        suffix = f" ({n} attachment{'s' if n != 1 else ''} included)" if n > 0 else ""
        
        return f"Message delivered to user successfully. Task completed. No further action needed.{suffix}"
    
    # ==================== Plan Mode 工具 ====================
    
    async def _tool_enter_plan_mode(self) -> str:
        """进入计划模式"""
        try:
            from tools.plan_mode_tool import PlanModeManager, PlanMode
            
            result = PlanModeManager.enter_plan_mode()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_exit_plan_mode(self, plan_title: str = None, 
                                    plan_description: str = None,
                                    steps: List[Dict] = None) -> str:
        """退出计划模式"""
        try:
            from tools.plan_mode_tool import PlanModeManager
            
            plan_data = None
            if plan_title and plan_description:
                PlanModeManager.create_plan(plan_title, plan_description, steps or [])
                plan_data = {
                    "title": plan_title,
                    "description": plan_description,
                    "steps": steps or []
                }
            
            result = PlanModeManager.exit_plan_mode(plan_data)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_create_plan(self, title: str, description: str = "", 
                                 steps: List[Dict] = None) -> str:
        """创建计划"""
        try:
            from tools.plan_mode_tool import PlanModeManager, PlanMode
            
            if PlanModeManager.get_mode() != PlanMode.PLAN:
                return json.dumps({
                    "success": False,
                    "error": "Not in plan mode. Use enter_plan_mode first."
                }, ensure_ascii=False, indent=2)
            
            if not title:
                return "Error: title is required"
            
            plan = PlanModeManager.create_plan(title, description, steps or [])
            
            return json.dumps({
                "success": True,
                "plan": plan.to_dict(),
                "message": f"Created plan: {plan.title}"
            }, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_get_plan(self) -> str:
        """获取当前计划"""
        try:
            from tools.plan_mode_tool import PlanModeManager
            
            plan = PlanModeManager.get_current_plan()
            
            if not plan:
                return json.dumps({
                    "success": True,
                    "plan": None,
                    "message": "No current plan"
                }, ensure_ascii=False, indent=2)
            
            return json.dumps({
                "success": True,
                "plan": plan.to_dict(),
                "mode": PlanModeManager.get_mode().value,
                "message": f"Current plan: {plan.title}"
            }, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    # ==================== Tool Search 工具 ====================
    
    async def _tool_tool_search(self, query: str, max_results: int = 5) -> str:
        """搜索可用工具"""
        if not query:
            return "Error: No query provided"
        
        import re
        
        try:
            from tools.tool_registry import ToolRegistry
            
            all_tools = list(ToolRegistry._tools.values())
            
            if query.startswith("select:"):
                tool_name = query[7:].strip()
                for tool in all_tools:
                    if tool.name.lower() == tool_name.lower():
                        return json.dumps({
                            "matches": [tool.name],
                            "selected_tool": {
                                "name": tool.name,
                                "description": tool.description[:500] if tool.description else ""
                            }
                        }, ensure_ascii=False, indent=2)
                
                return json.dumps({
                    "matches": [],
                    "error": f"Tool not found: {tool_name}"
                }, ensure_ascii=False, indent=2)
            
            # 关键词搜索
            query_lower = query.lower()
            query_words = re.findall(r'\w+', query_lower)
            
            scored_tools = []
            for tool in all_tools:
                score = 0
                name_lower = tool.name.lower()
                desc_lower = (tool.description or "").lower()
                
                if query_lower == name_lower:
                    score += 100
                if query_lower in name_lower:
                    score += 50
                for word in query_words:
                    if word in name_lower:
                        score += 20
                    if word in desc_lower:
                        score += 5
                
                if score > 0:
                    scored_tools.append((tool, score))
            
            scored_tools.sort(key=lambda x: x[1], reverse=True)
            matches = [tool.name for tool, score in scored_tools[:max_results]]
            
            return json.dumps({
                "matches": matches,
                "query": query,
                "total_deferred_tools": len(all_tools)
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: ToolRegistry not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    async def _tool_list_tools(self) -> str:
        """列出所有可用工具"""
        try:
            from tools.tool_registry import ToolRegistry
            
            all_tools = list(ToolRegistry._tools.values())
            
            tools_info = []
            for tool in all_tools:
                tools_info.append({
                    "name": tool.name,
                    "description": (tool.description or "")[:200],
                    "requires_permission": getattr(tool, "requires_permission", True),
                    "danger_level": getattr(tool, "danger_level", "medium")
                })
            
            tools_info.sort(key=lambda x: x["name"])
            
            return json.dumps({
                "tools": tools_info,
                "count": len(tools_info)
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: ToolRegistry not available"
        except Exception as e:
            return f"Error: {str(e)}"
    
    # ==================== LSP 代码智能工具（9 种操作） ====================
    
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
            from .lsp_client import LspClient, get_lsp_client
            
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
    
    # ==================== LSP 结果格式化 ====================
    
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
        from .lsp_client import LspLocation, uri_to_relative_path
        
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
        from .lsp_client import LspSymbolInfo, uri_to_relative_path
        
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
        from .lsp_client import LspCallHierarchyItem, uri_to_relative_path
        
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
        from .lsp_client import LspCallHierarchyCall, LspCallHierarchyItem, uri_to_relative_path
        
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
