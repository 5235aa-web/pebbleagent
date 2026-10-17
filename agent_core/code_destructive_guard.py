"""
代码破坏性行为扫描

拦截高置信度的破坏性命令字面量（rm -rf /、del /s /q C:\、format C: 等）。

定位：纵深防御的一层——只拦截高置信度的破坏性模式（字面量级），
完整边界由权限层（EXEC_COMMAND 询问/信任）与各语言验证器承担。

使用方式:
    is_blocked, patterns = scan_code_for_destructive_behavior(code, language)
"""

from __future__ import annotations

import re
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)


# 高置信度破坏性模式（代码文本级扫描，与语言无关）
_DESTRUCTIVE_PATTERNS: List[Tuple[str, str]] = [
    # Unix 根级/主目录删除
    (r'\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+/(?:\s|$|["\'`;)])', "rm -rf /"),
    (r'\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+~', "rm -rf ~"),
    # Windows cmd 盘根递归删除（del /s /q C:\ 形态）
    (r'\bdel\s+/[sS]\b[^\r\n]*?\s+[A-Za-z]:[\\/]*\s*(?:$|["\'\n;])', "del /s <drive root>"),
    (r'\b(rd|rmdir)\s+/[sS]\b[^\r\n]*?\s+[A-Za-z]:[\\/]*\s*(?:$|["\'\n;])', "rd /s <drive root>"),
    # 格式化 / 磁盘设备
    (r'\bformat\s+[A-Za-z]:', "format <drive>"),
    (r'\bmkfs(\.\w+)?\b', "mkfs"),
    (r'\bdd\s+if=[^\r\n]*\bof=/dev/', "dd of=/dev/"),
    # 代码级根目录递归删除（Python）
    (r'\b(shutil\.rmtree|os\.rmdir)\s*\(\s*[\'"](?:/|[A-Za-z]:[\\/]*)[\'"]',
     "rmtree/rmdir on root"),
    # PowerShell 磁盘命令（经代码字符串调用）
    (r'\b(Format-Volume|Clear-Disk|Initialize-Disk)\b', "PS disk command"),
    (r'\b(vssadmin|wbadmin)\b[^\r\n]*\bdelete\b', "backup delete"),
]


def scan_code_for_destructive_behavior(code: str, language: str = "") -> Tuple[bool, List[str]]:
    """
    扫描代码中的高置信度破坏性模式

    Args:
        code: 代码文本
        language: 语言标识（仅用于日志；模式与语言无关）

    Returns:
        (is_blocked, matched_patterns) —— 命中任一模式即 block
    """
    if not code:
        return False, []

    detected: List[str] = []
    for pattern, desc in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            detected.append(desc)

    if detected:
        logger.warning(
            f"[DestructiveGuard] Detected destructive pattern(s)! Language={language}: {detected}"
        )
        return True, detected

    return False, []
