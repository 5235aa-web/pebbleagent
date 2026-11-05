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
    # 格式化 / 磁盘设备 —— 这些词在 README、教程、错误提示里被正常引用，
    # 只有真的被调用才拦；`rm -rf /` 这类留在上面做宽匹配，写出来本身就可疑。
    (r'\bformat\s+[A-Za-z]:', "format <drive>"),
    (r'(?:subprocess\.\w+|os\.system|os\.popen|Popen|check_output|check_call|run)\s*\([^\n]{0,240}?\b(?:mkfs(?:\.\w+)?|dd\s+if=[^\n]{0,120}?\bof=/dev/|Format-Volume|Clear-Disk|Initialize-Disk)\b',
     "system: disk command via subprocess"),
    # 代码级根目录递归删除（Python）
    (r'\b(shutil\.rmtree|os\.rmdir)\s*\(\s*[\'"](?:/|[A-Za-z]:[\\/]*)[\'"]',
     "rmtree/rmdir on root"),
    (r'(?:subprocess\.\w+|os\.system|os\.popen|Popen|check_output|check_call|run)\s*\([^\n]{0,240}?\b(?:vssadmin|wbadmin)\b[^\n]{0,120}?\bdelete\b',
     "system: backup delete via subprocess"),
    # 构建/打包命令只在「真的被执行」时才算：字符串常量、注释、文档里提到
    # 这些词没有意义，宽匹配会把测试和说明文字一起拦掉。
    (r'(?:subprocess\.\w+|os\.system|os\.popen|Popen|check_output|check_call|run)\s*\([^\n]{0,240}?(?:tauri\s+build|npm\s+run\s+(?:build|release|publish)|(?:npx\s+)?(?:tauri|vite)\s+build|\bpyinstaller\b)', "build/packaging: via subprocess"),
    (r'(?:child_process\.(?:exec|spawn|execSync|spawnSync)|execSync)\s*\([^\n]{0,240}?(?:tauri\s+build|npm\s+run\s+(?:build|release|publish)|(?:npx\s+)?(?:tauri|vite)\s+build|\bpyinstaller\b)', "build/packaging: via child_process"),
    # argv 数组形态（subprocess.run(["npm", "run", "build"])）：数组里没有连续的
    # `npm run build` 字符串，上面两条都看不见，只有数组本身能命中。
    # 三种组合分开写，build 词必须紧跟在对应动词后面：`["python","build","--release"]`
    # 和 `["python","-m","pytest","-k","build"]` 都不是打包，宽匹配会把它们一起拦掉。
    (r'\[\s*["\'](?:npm|pnpm|yarn)["\']\s*,\s*["\']run["\']\s*,\s*["\'](?:build|release|publish)["\']',
     "build/packaging: argv list (run build)"),
    (r'\[\s*["\'](?:npx|cargo)["\']\s*,\s*["\'](?:tauri|vite)["\']\s*,\s*["\']build["\']',
     "build/packaging: argv list (tauri/vite build)"),
    (r'\[\s*["\']python\d?["\']\s*,\s*["\']-m["\']\s*,\s*["\']pyinstaller["\']',
     "build/packaging: argv list (python -m PyInstaller)"),
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
