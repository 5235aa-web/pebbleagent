"""
6 个专用验证器：
1. read_only_check     - 判断命令是否纯只读（可并发执行）
2. destructive_check   - 检测破坏性操作（rm -rf / dd / mkfs 等）
3. mode_check          - 检测 shell 模式（set -e / set -u / set -x 等）
4. sed_check           - sed -i 原地修改的特殊验证
5. path_check          - 扫描命令中的路径，检查是否越界
6. semantic_check      - 分析命令意图（读/写/系统/网络/进程/文件系统）

集成点：
- permission_enforcer.py：check_dangerous_command() → BashValidator.destructive_check()
- tool_executor_impl._tool_execute_shell()：执行前 → BashValidator.validate()
- tool_concurrency.py：check_bash_safety() → BashValidator.read_only_check()
"""

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Set
import logging

logger = logging.getLogger(__name__)


# ==================== 数据类 ====================

@dataclass
class BashValidationReport:
    """Bash 命令验证统一报告"""
    command: str                          # 原始命令
    is_read_only: bool = True             # 是否只读（可并发执行）
    is_destructive: bool = False          # 是否有破坏性操作
    blocked: bool = False                 # 是否应完全阻止执行
    block_reason: str = ""                # 阻止原因
    warnings: List[str] = field(default_factory=list)  # 警告列表
    intent: str = "unknown"               # 命令意图
    target_paths: List[str] = field(default_factory=list)  # 涉及的文件路径
    path_violations: List[str] = field(default_factory=list)  # 越界路径
    suggested_sandbox: bool = False       # 是否建议沙箱执行
    has_sed_inplace: bool = False         # sed -i 原地修改
    has_pipe_write: bool = False          # 管道后写入（| tee file, >, >>)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "is_read_only": self.is_read_only,
            "is_destructive": self.is_destructive,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "warnings": self.warnings,
            "intent": self.intent,
            "target_paths": self.target_paths,
            "path_violations": self.path_violations,
            "suggested_sandbox": self.suggested_sandbox,
        }


# ==================== BashValidator ====================

class BashValidator:
    """
    Bash 命令验证器

    使用方式:
        validator = BashValidator(project_path="E:\\agentspace")
        report = validator.validate("rm -rf /tmp/test")
        if report.blocked:
            raise PermissionError(report.block_reason)
    """

    def __init__(self, project_path: str = "E:\\agentspace"):
        self._project_path = os.path.abspath(project_path)
        self._path_whitelist: List[str] = []

    def set_path_whitelist(self, paths: List[str]) -> None:
        """设置额外白名单路径"""
        if not paths:
            return
        self._path_whitelist = [os.path.abspath(os.path.expandvars(p)) for p in paths if p]

    # ==================== 主入口 ====================

    def validate(self, command: str) -> BashValidationReport:
        """运行全部 6 个验证器，返回统一报告"""
        report = BashValidationReport(command=command)

        self._read_only_check(command, report)
        self._destructive_check(command, report)
        self._mode_check(command, report)
        self._sed_check(command, report)
        self._path_check(command, report)
        self._semantic_check(command, report)

        return report

    # ==================== 1. 只读检查 ====================

    # 白名单：明确只读的命令
    _READ_ONLY_WHITELIST: Set[str] = {
        "ls", "dir", "cat", "head", "tail", "less", "more",
        "grep", "find", "echo", "pwd", "whoami", "wc", "sort",
        "uniq", "diff", "stat", "du", "df", "env", "printenv",
        "which", "where", "whereis", "type", "file", "basename",
        "dirname", "realpath", "readlink", "date", "hostname",
        "uname", "uptime", "id", "groups", "who", "w", "last",
        "history", "alias", "type", "help", "man", "info",
        "whatis", "apropos", "locate", "updatedb", "xargs",
        "cut", "tr", "paste", "join", "split", "tee",
        "od", "hexdump", "xxd",
    }

    # 灰名单：需要分析参数才能判断的命令
    _READ_ONLY_GRAYLIST: Set[str] = {
        "git", "npm", "npx", "pip", "python", "python3", "node",
        "docker", "kubectl", "cargo", "go", "rustc", "gcc", "g++",
        "make", "cmake", "tar", "zip", "unzip", "gzip", "gunzip",
        "ssh", "scp", "rsync", "curl", "wget",
    }

    # 黑名单：明确写入的命令
    _WRITE_BLACKLIST: Set[str] = {
        "rm", "mv", "cp", "chmod", "chown", "chgrp", "touch",
        "mkdir", "rmdir", "ln", "dd", "mkfs", "fdisk", "mount",
        "umount", "kill", "killall", "reboot", "shutdown",
        "halt", "poweroff", "systemctl", "service", "apt",
        "apt-get", "yum", "dnf", "pacman", "brew", "choco",
        "pip3", "npm", "npx",
    }

    # 写入相关的子命令关键词（用于分析灰名单命令的参数）
    _WRITE_SUBCOMMANDS: Set[str] = {
        "push", "commit", "add", "install", "uninstall", "update",
        "upgrade", "run", "start", "stop", "restart", "build",
        "deploy", "publish", "create", "delete", "remove",
        "rename", "move", "copy", "exec", "execute", "apply",
        "set", "init", "clean", "rebuild", "reset", "enable",
        "disable", "login", "signin", "put", "post", "patch",
    }

    def _read_only_check(self, command: str, report: BashValidationReport) -> None:
        """
        判断命令是否纯只读

        三层判断：
        1. 白名单 → 只读
        2. 管道/重定向分析 → 检测写入
        3. 灰名单参数分析 → 根据子命令判断
        4. 黑名单 → 非只读
        """
        # 检测管道写入和重定向
        if self._has_write_redirect(command):
            report.is_read_only = False
            report.has_pipe_write = True
            report.warnings.append("命令包含写重定向（>、>>、| tee 等），非只读")
            return

        # 尝试提取主命令
        main_cmd = self._extract_main_command(command)
        if not main_cmd:
            return

        # 白名单检查
        if main_cmd in self._READ_ONLY_WHITELIST:
            report.is_read_only = True
            return

        # 黑名单检查
        if main_cmd in self._WRITE_BLACKLIST:
            report.is_read_only = False
            return

        # 灰名单分析
        if main_cmd in self._READ_ONLY_GRAYLIST:
            sub_cmd = self._extract_sub_command(command, main_cmd)
            if sub_cmd and sub_cmd in self._WRITE_SUBCOMMANDS:
                report.is_read_only = False
            elif sub_cmd in ("log", "status", "list", "ls", "show", "info", "get",
                             "search", "find", "view", "describe", "explain", "check",
                             "diff", "version", "--version", "-v", "help", "--help", "-h"):
                report.is_read_only = True
            else:
                # 无法判断，保守处理为非只读
                report.is_read_only = False

    def _has_write_redirect(self, command: str) -> bool:
        """检测写重定向和管道写入"""
        patterns = [
            r'\|\s*tee\b',         # | tee file
            r'>[^>]',              # >（但不匹配 >>）
            r'\|\s*dd\b.*\bof=',   # | dd of=...
            r'[^>]>>[^>]',         # >>（追加）
        ]
        for p in patterns:
            if re.search(p, command):
                return True
        return False

    def _extract_main_command(self, command: str) -> Optional[str]:
        """提取主命令名"""
        clean = command.strip()
        if not clean:
            return None
        # 去掉前导的 set -e 等
        clean = re.sub(r'^(?:set\s+[-+]\S+\s*;?\s*)+', '', clean).strip()
        # 分号分隔取第一个有意义的部分
        try:
            parts = shlex.split(clean)
        except ValueError:
            parts = clean.split()
        if not parts:
            return None
        main = os.path.basename(parts[0]).lower()
        # 去掉 .exe / .cmd 后缀
        if main.endswith('.exe') or main.endswith('.cmd'):
            main = main[:-4]
        return main

    def _extract_sub_command(self, command: str, main_cmd: str) -> Optional[str]:
        """提取子命令（如 git push 中的 push）"""
        clean = command.strip()
        try:
            parts = shlex.split(clean)
        except ValueError:
            parts = clean.split()
        # 找到 main_cmd 之后的下一个非选项参数
        found_main = False
        for part in parts:
            base = os.path.basename(part).lower()
            if base.endswith('.exe') or base.endswith('.cmd'):
                base = base[:-4]
            if not found_main and base == main_cmd:
                found_main = True
                continue
            if found_main and not part.startswith('-'):
                return part.lower()
        return None

    # ==================== 2. 破坏性检查 ====================

    _DESTRUCTIVE_BLOCK: List[Tuple[str, str]] = [
        # 立即阻止（🔴）
        (r'\brm\s+-rf\s+/', "禁止递归强制删除根目录 (rm -rf /)"),
        (r'\brm\s+-rf\s+~', "禁止递归强制删除主目录 (rm -rf ~)"),
        (r'\brm\s+-rf\s+\$HOME', "禁止递归强制删除 HOME 目录"),
        (r'\bdd\s+if=.*\s+of=/dev/', "禁止直接写入磁盘设备 (dd of=/dev/...)"),
        (r'\bmkfs\.', "禁止格式化文件系统 (mkfs)"),
        (r'\bfdisk\s+/dev/', "禁止磁盘分区 (fdisk)"),
        (r'\bchmod\s+-R\s+777\s+/', "禁止递归开放根目录权限 (chmod -R 777 /)"),
        (r'\bchown\s+-R\s+\S+\s+/', "禁止递归修改根目录所有权 (chown -R /)"),
        (r':\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:', "禁止 fork 炸弹"),
        (r'\bcurl\b.*\|\s*(?:ba)?sh\b', "禁止远程脚本管道执行 (curl | sh)"),
        (r'\bwget\b.*\|\s*(?:ba)?sh\b', "禁止远程脚本管道执行 (wget | sh)"),
    ]

    _DESTRUCTIVE_WARN: List[Tuple[str, str]] = [
        # 警告但允许（🟡）
        (r'\brm\s+-rf\b', "递归强制删除 (rm -rf)"),
        (r'\brm\s+-fr\b', "递归强制删除 (rm -fr)"),
        (r'\brmdir\s+/[sS]\b', "递归删除目录 (rmdir /s)"),
        (r'\bdel\s+/[sS]\b', "递归删除文件 (del /s)"),
        (r'\bsudo\b', "权限提升 (sudo)"),
        (r'\bsu\b(?!\w*do)', "切换用户 (su)"),
        (r'\brunas\b', "以其他用户身份运行 (runas)"),
        (r'\bshutdown\b', "系统关机 (shutdown)"),
        (r'\breboot\b', "系统重启 (reboot)"),
        (r'\bhalt\b', "系统停止 (halt)"),
        (r'\bpoweroff\b', "系统断电 (poweroff)"),
        (r'\biptables\b', "防火墙配置 (iptables)"),
        (r'\bnetsh\b.*\bfirewall\b', "Windows 防火墙配置 (netsh firewall)"),
        (r'\buseradd\b', "添加系统用户 (useradd)"),
        (r'\buserdel\b', "删除系统用户 (userdel)"),
        (r'\bpasswd\b', "修改系统密码 (passwd)"),
        (r'\bnet\s+user\b', "Windows 用户管理 (net user)"),
        (r'\bcat\s+.*\.env\b', "读取环境变量文件 (cat .env)"),
        (r'\btype\s+.*\.env\b', "读取环境变量文件 (type .env)"),
        (r'\|\s*rm\b', "管道到删除命令 (| rm)"),
        (r'\|\s*sudo\b', "管道到 sudo (| sudo)"),
        (r'\bInvoke-Expression\b', "PowerShell 动态代码执行 (Invoke-Expression)"),
        (r'\bIEX\b', "PowerShell IEX 动态代码执行"),
        (r'\bREG\s+(?:ADD|DELETE)\b', "Windows 注册表修改", re.IGNORECASE),
    ]

    def _destructive_check(self, command: str, report: BashValidationReport) -> None:
        """
        检测破坏性操作

        两级：
        🔴 blocked=True → 立即阻止
        🟡 warning → 记录警告但不阻止
        """
        cmd_lower = command.lower().strip()

        # 先检查阻止级
        for pattern, reason in self._DESTRUCTIVE_BLOCK:
            flags = re.IGNORECASE if isinstance(pattern, tuple) else 0
            if re.search(pattern, cmd_lower, flags):
                report.is_destructive = True
                report.blocked = True
                report.block_reason = reason
                report.warnings.append(f"🔴 {reason}")
                return

        # 再检查警告级
        for item in self._DESTRUCTIVE_WARN:
            pattern, reason = item[0], item[1]
            flags = re.IGNORECASE if len(item) > 2 and item[2] == re.IGNORECASE else 0
            if re.search(pattern, cmd_lower, flags):
                report.is_destructive = True
                report.warnings.append(f"🟡 {reason}")

    # ==================== 3. Shell 模式检查 ====================

    def _mode_check(self, command: str, report: BashValidationReport) -> None:
        """
        检测 shell 选项模式

        - set -e: 错误即退出 → 安全，推荐
        - set -u: 未定义变量报错 → 安全
        - set -x: 打印每步 → 仅调试
        - set +e: 忽略错误 → 危险，可能导致级联失败
        """
        for m in re.finditer(r'set\s+([-+])(\S+)', command):
            sign = m.group(1)
            options = m.group(2)
            if sign == '-':
                if 'e' in options:
                    report.warnings.append("set -e：错误即退出（安全模式）")
                if 'u' in options:
                    report.warnings.append("set -u：未定义变量报错（安全模式）")
                if 'x' in options:
                    report.warnings.append("set -x：调试模式")
            elif sign == '+':
                if 'e' in options:
                    report.warnings.append("🟡 set +e：忽略错误可能导致级联失败")
                    report.suggested_sandbox = True

    # ==================== 4. sed 专项检查 ====================

    def _sed_check(self, command: str, report: BashValidationReport) -> None:
        """
        sed 命令专项验证

        - sed -i: 原地修改 → 非只读
        - sed -i.bak: 有备份的原地修改 → 较安全
        - sed 's/.../.../': 管道输入 → 只读
        """
        if not re.search(r'\bsed\b', command, re.IGNORECASE):
            return
        if re.search(r'\bsed\s+.*-i(?!\.\w+\b)', command):
            report.has_sed_inplace = True
            report.is_read_only = False
            report.warnings.append("🟡 sed -i：原地修改文件")
        elif re.search(r'\bsed\s+.*-i\.\w+\b', command):
            report.has_sed_inplace = True
            report.warnings.append("sed -i.bak：原地修改但有备份文件")

    # ==================== 5. 路径检查 ====================

    def _path_check(self, command: str, report: BashValidationReport) -> None:
        """
        扫描命令中的路径，检查是否越界

        使用正则提取所有类路径字符串，对每个路径进行边界检查。
        特殊处理：/dev/null、/tmp/、/proc/、/sys/ 等系统路径。
        """
        # 提取类路径字符串
        path_patterns = [
            r'(?:^|\s)(/[^\s;|&<>`\'"]+)',     # 绝对路径 /xxx
            r'(?:^|\s)(~[^\s;|&<>`\'"]*)',     # ~/xxx
            r'(?:^|\s)([A-Za-z]:\\[^\s;|&<>`\'"]+)',  # Windows 绝对路径 C:\...
            r'(?:^|\s)(\.{1,2}/[^\s;|&<>`\'"]+)',     # 相对路径 ./../
        ]
        paths = []
        for pat in path_patterns:
            for m in re.finditer(pat, command):
                p = m.group(1).strip()
                if p:
                    paths.append(p)

        report.target_paths = list(set(paths))

        # 系统安全路径（不需要检查）
        safe_prefixes = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
                         "/tmp/", "/proc/", "/sys/", "/run/")

        for p in report.target_paths:
            if any(p.startswith(sp) for sp in safe_prefixes):
                continue
            if not self._is_path_allowed(p):
                report.path_violations.append(p)
                report.warnings.append(f"🔴 路径越界: {p}")
                if not report.blocked:
                    report.blocked = True
                    report.block_reason = f"命令访问了项目目录外的路径: {p}"

    def _is_path_allowed(self, path: str) -> bool:
        """检查路径是否在项目目录内，或是否在白名单中"""
        try:
            expanded = os.path.expanduser(os.path.expandvars(path))
            abs_path = os.path.abspath(expanded)
            abs_project = os.path.abspath(self._project_path)

            # 项目目录内
            if abs_path.lower().startswith(abs_project.lower()):
                return True

            # 白名单路径
            for wp in self._path_whitelist:
                if abs_path.lower().startswith(wp.lower()):
                    return True

            return False
        except Exception:
            return False

    # ==================== 6. 语义检查 ====================

    _INTENT_PATTERNS: List[Tuple[str, str, str]] = [
        # (正则, 意图类别, 描述)
        (r'\b(systemctl|service|systemd)\b', "system", "系统服务管理"),
        (r'\b(apt|apt-get|yum|dnf|pacman|brew|choco|pip\s+install|npm\s+install)\b', "system", "包管理器安装"),
        (r'\b(curl|wget|nc|netcat|ssh|scp|sftp|ftp|telnet|ping|traceroute)\b', "network", "网络操作"),
        (r'\b(kill|killall|pkill|ps|top|htop|nice|renice)\b', "process", "进程管理"),
        (r'\b(mount|umount|df|du|mkfs|fdisk|lsblk)\b', "filesystem", "文件系统操作"),
        (r'\b(git\s+push|git\s+commit|rm\s+|mv\s+|cp\s+|mkdir|rmdir|touch|dd)\b', "file_write", "文件写入/删除"),
        (r'\b(ls\b|cat\b|head\b|tail\b|grep\b|find\b|less\b|more\b)', "file_read", "文件读取"),
    ]

    def _semantic_check(self, command: str, report: BashValidationReport) -> None:
        """分析命令意图：读/写/系统/网络/进程/文件系统"""
        cmd_lower = command.lower()
        found_intents = []

        for pattern, intent, desc in self._INTENT_PATTERNS:
            if re.search(pattern, cmd_lower):
                found_intents.append(intent)
                if intent in ("system", "network", "process", "filesystem", "file_write"):
                    report.suggested_sandbox = True

        if found_intents:
            report.intent = found_intents[0]  # 主要意图
        else:
            report.intent = "unknown"


# ==================== 便捷函数（向后兼容原有 _check_dangerous_command 接口） ====================

def get_bash_validator(project_path: str = "E:\\agentspace") -> BashValidator:
    """获取 BashValidator 实例"""
    return BashValidator(project_path)
