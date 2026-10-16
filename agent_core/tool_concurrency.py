"""
Tool Concurrency - 工具并发判断系统

实现智能的工具并发执行：
1. isConcurrencySafe - 判断工具是否可以安全并发执行
2. ToolConcurrencyCategory - 工具并发类别
3. partitionToolCalls - 将工具调用分区为并发安全和非安全批次

核心原则：
- 只读工具可以并发执行（read_file, grep, glob, ls 等）
- 写操作工具必须串行执行（write_file, edit_file, execute_shell 等）
- 有副作用的工具必须串行执行
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional, Set
from enum import Enum
import re

from .safe_json import safe_json_loads


class ToolConcurrencyCategory(Enum):
    """
    工具并发类别
    
    READ_ONLY: 只读工具，可以安全并发执行
    WRITE: 写操作工具，必须串行执行
    SIDE_EFFECT: 有副作用的工具，必须串行执行
    UNKNOWN: 未知工具，保守处理为串行
    """
    READ_ONLY = "read_only"
    WRITE = "write"
    SIDE_EFFECT = "side_effect"
    UNKNOWN = "unknown"


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    category: ToolConcurrencyCategory
    # 额外的并发安全判断函数（可选）
    # 有些工具根据参数决定是否安全
    safety_check: Optional[callable] = None


# ==================== 工具注册表 ====================

# 只读工具列表 - 可以安全并发执行
READ_ONLY_TOOLS: Set[str] = {
    # 文件读取
    "read_file",
    "get_file_info",
    
    # 目录列表
    "list_directory",
    "ls",
    
    # 搜索工具
    "search_in_file",
    "search_files",
    "find_files",
    "grep",
    "glob",
    
    # Git 只读操作
    "git_status",
    "git_log",
    "git_diff",
    
    # 网络获取 — 2026-10-16: 浏览器回退已移除（纯 httpx），fetch_url 恢复只读并行
    "fetch_url",
    
    # 系统信息
    "get_env_var",
    "get_system_info",
    "list_processes",
    
    # 技能和工具查询
    "list_skills",
    "list_tools",
    "tool_search",
    "get_plan",
    
    # LSP 代码智能（全部只读）
    "lsp_go_to_definition",
    "lsp_find_references",
    "lsp_hover",
    "lsp_document_symbol",
    "lsp_workspace_symbol",
    "lsp_go_to_implementation",
    "lsp_prepare_call_hierarchy",
    "lsp_incoming_calls",
    "lsp_outgoing_calls",
    
    # Todo 读取
    "todo_read",
}

# 写操作工具列表 - 必须串行执行
WRITE_TOOLS: Set[str] = {
    # 文件写入
    "write_file",
    "edit_file",
    "delete_file",
    "copy_file",
    "move_file",
    
    # 目录操作
    "create_directory",
    
    # 代码执行
    "execute_code",
    "execute_shell",
    "powershell",
    
    # 环境变量设置
    "set_env_var",
    
    # 进程管理
    "kill_process",
    
    # Git 写操作（如果有）
    "git_commit",
    "git_push",
    "git_checkout",
    
    # 技能加载
    "load_skill",
    
    # Todo 写入
    "todo_write",
    
    # Plan 模式（进入/退出为会话状态变更，归 SIDE_EFFECT_TOOLS；2026-10-16 对齐）
    "create_plan",
    
    # Brief 消息
    "brief",
    
    # 网络搜索（外部 API 礼貌串行）
    "web_search",
    "browser_navigate",
    "browser_download",
}

# 有副作用的工具 - 必须串行执行
SIDE_EFFECT_TOOLS: Set[str] = {
    # 这些工具会改变系统状态或需要用户交互
    "ask_user_question",  # 需要等待用户响应
    "enter_plan_mode",    # 改变会话模式
    "exit_plan_mode",     # 改变会话模式
}


# ==================== 特殊工具的安全检查函数 ====================

def check_bash_safety(args: Dict[str, Any]) -> bool:
    """
    检查 Bash/Shell 命令是否安全并发
    
    委托给 BashValidator.read_only_check() 执行完整的三层判断：
    1. 白名单检查（ls/cat/grep 等）
    2. 管道/重定向分析
    3. 灰名单参数分析（git/npm 等的子命令）
    """
    command = args.get("command", "")
    if not command:
        return False
    
    try:
        from .bash_validator import BashValidator
        validator = BashValidator()
        report = validator.validate(command)
        return report.is_read_only
    except ImportError:
        # 回退到旧逻辑
        read_only_patterns = [
            r'^ls\b', r'^cat\b', r'^head\b', r'^tail\b',
            r'^grep\b', r'^find\b', r'^which\b', r'^echo\b',
            r'^pwd$', r'^whoami$', r'^git status', r'^git log',
            r'^git diff', r'^git branch',
            r'^python --version', r'^node --version',
            r'^npm list', r'^pip list',
        ]
        for pattern in read_only_patterns:
            if re.match(pattern, command.strip(), re.IGNORECASE):
                return True
        return False


def check_powershell_safety(args: Dict[str, Any]) -> bool:
    """
    检查 PowerShell 命令是否安全并发
    """
    command = args.get("command", "")
    
    # 只读命令模式
    read_only_patterns = [
        r'^Get-',
        r'^Test-Path',
        r'^Get-Content',
        r'^Get-ChildItem',
        r'^Get-Location',
        r'^Get-Process',
        r'^Get-Service',
        r'^Write-Host',  # 只是输出，无副作用
    ]
    
    for pattern in read_only_patterns:
        if re.match(pattern, command.strip(), re.IGNORECASE):
            return True
    
    return False


def check_grep_safety(args: Dict[str, Any]) -> bool:
    """
    grep 总是只读的
    """
    return True


def check_glob_safety(args: Dict[str, Any]) -> bool:
    """
    glob 总是只读的
    """
    return True


def check_read_file_safety(args: Dict[str, Any]) -> bool:
    """
    read_file 总是只读的
    """
    return True


# 特殊工具的安全检查映射
TOOL_SAFETY_CHECKS: Dict[str, callable] = {
    "execute_shell": check_bash_safety,
    "execute_code": lambda args: False,  # 代码执行总是不安全
    "powershell": check_powershell_safety,
    "grep": check_grep_safety,
    "glob": check_glob_safety,
    "read_file": check_read_file_safety,
}


# ==================== 核心函数 ====================

def get_tool_category(tool_name: str) -> ToolConcurrencyCategory:
    """
    获取工具的并发类别
    
    Args:
        tool_name: 工具名称
    
    Returns:
        工具并发类别
    """
    if tool_name in READ_ONLY_TOOLS:
        return ToolConcurrencyCategory.READ_ONLY
    elif tool_name in WRITE_TOOLS:
        return ToolConcurrencyCategory.WRITE
    elif tool_name in SIDE_EFFECT_TOOLS:
        return ToolConcurrencyCategory.SIDE_EFFECT
    else:
        return ToolConcurrencyCategory.UNKNOWN


def is_concurrency_safe(tool_name: str, args: Dict[str, Any] = None) -> bool:
    """
    判断工具调用是否可以安全并发执行
    
    这是核心判断函数，决定工具是否可以与其他工具并行执行。
    
    Args:
        tool_name: 工具名称
        args: 工具参数（可选，用于更精细的判断）
    
    Returns:
        True 如果可以安全并发执行，False 否则
    """
    # 1. 首先检查是否有特殊的安全检查函数
    if tool_name in TOOL_SAFETY_CHECKS:
        try:
            return TOOL_SAFETY_CHECKS[tool_name](args or {})
        except Exception:
            return False
    
    # 2. MCP 工具：查询通过 annotations 注册的分类（最可靠的方法）
    mcp_cat = get_mcp_tool_concurrency(tool_name)
    if mcp_cat is not None:
        return mcp_cat == ToolConcurrencyCategory.READ_ONLY
    
    # 3. 根据工具类别判断
    category = get_tool_category(tool_name)
    
    if category == ToolConcurrencyCategory.READ_ONLY:
        return True
    elif category in (ToolConcurrencyCategory.WRITE, ToolConcurrencyCategory.SIDE_EFFECT):
        return False
    else:
        # 未知工具，保守处理为不安全
        return False


@dataclass
class ToolCallBatch:
    """工具调用批次"""
    is_concurrency_safe: bool
    tool_calls: List[Dict[str, Any]]
    
    def __len__(self) -> int:
        return len(self.tool_calls)


def partition_tool_calls(
    tool_calls: List[Dict[str, Any]]
) -> List[ToolCallBatch]:
    """
    将工具调用分区为并发安全和非安全批次
    
    分区规则：
    1. 连续的并发安全工具放在同一批次（可并行执行）
    2. 非并发安全工具单独成批次（串行执行）
    
    这样可以最大化并行度，同时保证安全性。
    
    Args:
        tool_calls: 工具调用列表，每个元素包含 name 和 arguments
    
    Returns:
        分区后的批次列表
    """
    if not tool_calls:
        return []
    
    batches: List[ToolCallBatch] = []
    
    for tool_call in tool_calls:
        tool_name = tool_call.get("name", tool_call.get("function", {}).get("name", "unknown"))
        tool_args = tool_call.get("arguments", tool_call.get("function", {}).get("arguments", {}))
        
        # 解析参数（如果是字符串）
        if isinstance(tool_args, str):
            tool_args = safe_json_loads(tool_args, default={})
        
        is_safe = is_concurrency_safe(tool_name, tool_args)
        
        # 如果当前工具是安全的，且上一个批次也是安全的，则合并
        if is_safe and batches and batches[-1].is_concurrency_safe:
            batches[-1].tool_calls.append(tool_call)
        else:
            # 否则创建新批次
            batches.append(ToolCallBatch(
                is_concurrency_safe=is_safe,
                tool_calls=[tool_call]
            ))
    
    return batches


def get_max_concurrency() -> int:
    """
    获取最大并发数
    
    可以通过环境变量配置
    """
    import os
    return int(os.getenv("PEBBLE_MAX_TOOL_CONCURRENCY", "10"))


# ==================== 工具定义注册 ====================

def register_tool(
    name: str,
    category: ToolConcurrencyCategory,
    safety_check: callable = None
):
    """
    注册工具到并发系统
    
    Args:
        name: 工具名称
        category: 并发类别
        safety_check: 可选的安全检查函数
    """
    global READ_ONLY_TOOLS, WRITE_TOOLS, SIDE_EFFECT_TOOLS, TOOL_SAFETY_CHECKS
    
    if category == ToolConcurrencyCategory.READ_ONLY:
        READ_ONLY_TOOLS.add(name)
    elif category == ToolConcurrencyCategory.WRITE:
        WRITE_TOOLS.add(name)
    elif category == ToolConcurrencyCategory.SIDE_EFFECT:
        SIDE_EFFECT_TOOLS.add(name)
    
    if safety_check:
        TOOL_SAFETY_CHECKS[name] = safety_check


def get_all_registered_tools() -> Dict[str, ToolConcurrencyCategory]:
    """
    获取所有已注册的工具及其类别
    
    Returns:
        工具名称到类别的映射
    """
    result = {}
    for name in READ_ONLY_TOOLS:
        result[name] = ToolConcurrencyCategory.READ_ONLY
    for name in WRITE_TOOLS:
        result[name] = ToolConcurrencyCategory.WRITE
    for name in SIDE_EFFECT_TOOLS:
        result[name] = ToolConcurrencyCategory.SIDE_EFFECT
    for name, cat in _MCP_TOOL_CATEGORIES.items():
        result[name] = cat
    return result


# ==================== MCP 工具动态并发分类 ====================

_MCP_TOOL_CATEGORIES: Dict[str, ToolConcurrencyCategory] = {}
"""MCP 工具并发分类注册表，由 mcp_manager 在工具加载时根据 annotations 填充"""


def register_mcp_tool_concurrency(tool_full_name: str, read_only_hint: Optional[bool] = None, 
                                   destructive_hint: Optional[bool] = None):
    """
    注册 MCP 工具的并发分类
    
    基于 MCP 协议标准 annotations 字段：
    - readOnlyHint: true → 只读，可并发
    - destructiveHint: true → 破坏性写入，必须串行
    - 两者都未设置 → 保守串行
    
    这是最可靠的分类方法——完全依赖 MCP 服务器自身的声明，不依赖任何关键词猜测。
    由 mcp_manager 在每一次 tools/list 响应后调用。
    """
    if read_only_hint is True and destructive_hint is not True:
        _MCP_TOOL_CATEGORIES[tool_full_name] = ToolConcurrencyCategory.READ_ONLY
    elif destructive_hint is True:
        _MCP_TOOL_CATEGORIES[tool_full_name] = ToolConcurrencyCategory.WRITE
    else:
        _MCP_TOOL_CATEGORIES[tool_full_name] = ToolConcurrencyCategory.UNKNOWN


def unregister_mcp_tool_concurrency(server_name: str):
    """当 MCP 服务器断开或工具列表变化时，清除对应服务器的所有注册"""
    prefix = f"mcp__{server_name.replace('-', '_').lower()}"
    to_remove = [k for k in _MCP_TOOL_CATEGORIES if k.startswith(prefix)]
    for k in to_remove:
        del _MCP_TOOL_CATEGORIES[k]


def get_mcp_tool_concurrency(tool_full_name: str) -> Optional[ToolConcurrencyCategory]:
    """获取 MCP 工具的并发分类，未注册则返回 None"""
    return _MCP_TOOL_CATEGORIES.get(tool_full_name)


# ==================== 调试和日志 ====================

def explain_concurrency_decision(tool_name: str, args: Dict[str, Any] = None) -> str:
    """
    解释工具的并发决策
    
    用于调试和日志
    """
    category = get_tool_category(tool_name)
    is_safe = is_concurrency_safe(tool_name, args)
    
    if tool_name in TOOL_SAFETY_CHECKS:
        check_func = TOOL_SAFETY_CHECKS[tool_name]
        check_result = check_func(args or {})
        return f"Tool '{tool_name}': category={category.value}, safety_check={check_result}, final={is_safe}"
    else:
        return f"Tool '{tool_name}': category={category.value}, final={is_safe}"


def print_partition_plan(batches: List[ToolCallBatch]) -> str:
    """
    打印分区计划
    
    用于调试
    """
    lines = ["Tool execution partition plan:"]
    for i, batch in enumerate(batches):
        mode = "PARALLEL" if batch.is_concurrency_safe else "SERIAL"
        tools = [tc.get("name", tc.get("function", {}).get("name", "unknown")) for tc in batch.tool_calls]
        lines.append(f"  Batch {i+1} [{mode}]: {tools}")
    return "\n".join(lines)
