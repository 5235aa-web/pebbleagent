"""
Auto Memory Paths - 记忆路径管理

1. 配置目录解析（支持环境变量覆盖）
2. 项目路径清理和安全化
3. 记忆目录路径计算
4. 路径验证和安全检查

存储结构：
~/.claude/
├── projects/
│   └── <sanitized-project-path>/
│       └── memory/
│           ├── MEMORY.md
│           └── *.md
├── agent-memory/
│   └── <agent-type>/
│       └── MEMORY.md
└── ...

支持环境变量：
- CLAUDE_CONFIG_DIR: 配置主目录
- CLAUDE_COWORK_MEMORY_PATH_OVERRIDE: 记忆目录覆盖（用于 Cowork）
- CLAUDE_CODE_REMOTE_MEMORY_DIR: 远程记忆目录
"""

import os
import re
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple
from functools import lru_cache

logger = logging.getLogger(__name__)


# ============================================================================
# 常量
# ============================================================================

# 默认配置目录
DEFAULT_CONFIG_HOME = '~/.claude'

# 记忆目录名
MEMORY_DIRNAME = 'memory'

# 索引文件名
ENTRYPOINT_NAME = 'MEMORY.md'


# ============================================================================
# 配置目录
# ============================================================================

def get_config_home() -> str:
    """
    获取配置主目录
    
    解析顺序：
    1. CLAUDE_CONFIG_DIR 环境变量
    2. 默认 ~/.claude
    
    Returns:
        配置主目录路径
    """
    env_path = os.environ.get('CLAUDE_CONFIG_DIR')
    if env_path:
        return os.path.expanduser(env_path)
    return os.path.expanduser(DEFAULT_CONFIG_HOME)


def get_memory_base_dir() -> str:
    """
    获取记忆基础目录
    
    解析顺序：
    1. CLAUDE_CODE_REMOTE_MEMORY_DIR 环境变量（远程模式）
    2. 配置主目录
    
    Returns:
        记忆基础目录路径
    """
    remote_dir = os.environ.get('CLAUDE_CODE_REMOTE_MEMORY_DIR')
    if remote_dir:
        return remote_dir
    return get_config_home()


# ============================================================================
# 路径清理和安全化
# ============================================================================

def sanitize_path(path: str) -> str:
    """
    清理路径，使其可以安全地用作文件名
    
    将路径中的特殊字符替换为下划线，确保：
    1. 可以安全地用作目录名
    2. 跨平台兼容（Windows/Linux/macOS）
    3. 长度限制
    
    Args:
        path: 原始路径
    
    Returns:
        清理后的路径
    """
    if not path:
        return 'default'
    
    # Windows 驱动器号处理（C:\ -> C_）
    # 先保存驱动器号
    drive_match = re.match(r'^([A-Za-z]):', path)
    
    # 替换驱动器号冒号
    if drive_match:
        path = path[:2].replace(':', '_') + path[2:]
    
    # 替换所有特殊字符（包括冒号）
    # 保留路径分隔符，稍后统一替换
    sanitized = re.sub(r'[<>:"|?*]', '_', path)
    
    # 统一路径分隔符为下划线
    sanitized = re.sub(r'[/\\]', '_', sanitized)
    
    # 移除连续下划线
    sanitized = re.sub(r'_+', '_', sanitized)
    
    # 移除首尾下划线
    sanitized = sanitized.strip('_')
    
    # 限制长度（文件系统限制）
    if len(sanitized) > 200:
        # 使用哈希缩短
        hash_suffix = hashlib.md5(path.encode('utf-8')).hexdigest()[:8]
        sanitized = sanitized[:190] + '_' + hash_suffix
    
    return sanitized or 'default'


def validate_memory_path(raw: Optional[str], expand_tilde: bool = False) -> Optional[str]:
    """
    验证并规范化记忆路径
    
    安全检查：
    1. 必须是绝对路径
    2. 不能是根目录或接近根目录
    3. 不能是 UNC 路径
    4. 不能包含空字节
    
    Args:
        raw: 原始路径
        expand_tilde: 是否展开 ~ 
    
    Returns:
        规范化后的路径（带尾部分隔符），或 None（无效）
    """
    if not raw:
        return None
    
    candidate = raw
    
    # 展开 ~/
    if expand_tilde and candidate.startswith('~/'):
        candidate = os.path.join(os.path.expanduser('~'), candidate[2:])
    
    # 规范化
    normalized = os.path.normpath(candidate)
    
    # 移除尾部分隔符，稍后统一添加
    normalized = normalized.rstrip(os.sep).rstrip('/')
    
    # 安全检查
    if not os.path.isabs(normalized):
        return None
    
    if len(normalized) < 3:
        # 太短，可能是根目录
        return None
    
    # Windows 驱动器号检查（C: 但不是 C:\path）
    if re.match(r'^[A-Za-z]:$', normalized):
        return None
    
    # UNC 路径检查
    if normalized.startswith('\\\\') or normalized.startswith('//'):
        return None
    
    # 空字节检查
    if '\0' in normalized:
        return None
    
    # 添加尾部分隔符
    return normalized + os.sep


# ============================================================================
# 项目目录
# ============================================================================

@lru_cache(maxsize=128)
def get_projects_dir() -> str:
    """
    获取项目存储目录
    
    Returns:
        项目目录路径
    """
    return os.path.join(get_config_home(), 'projects')


@lru_cache(maxsize=128)
def get_project_dir(project_path: str) -> str:
    """
    获取项目存储目录
    
    Args:
        project_path: 项目路径（工作目录）
    
    Returns:
        项目存储目录路径
    """
    return os.path.join(get_projects_dir(), sanitize_path(project_path))


# ============================================================================
# Auto Memory 路径
# ============================================================================

def get_auto_memory_dir(project_path: str) -> str:
    """
    获取 Auto Memory 目录路径
    
    解析顺序：
    1. CLAUDE_COWORK_MEMORY_PATH_OVERRIDE 环境变量
    2. settings.json 中的 autoMemoryDirectory
    3. <memoryBase>/projects/<sanitized-project>/memory/
    
    Args:
        project_path: 项目路径
    
    Returns:
        Auto Memory 目录路径
    """
    # 检查环境变量覆盖
    override = os.environ.get('CLAUDE_COWORK_MEMORY_PATH_OVERRIDE')
    if override:
        validated = validate_memory_path(override, expand_tilde=False)
        if validated:
            return validated
    
    # 默认路径
    projects_dir = os.path.join(get_memory_base_dir(), 'projects')
    return os.path.join(projects_dir, sanitize_path(project_path), MEMORY_DIRNAME) + os.sep


def get_memory_index_path(project_path: str) -> str:
    """
    获取记忆索引文件路径（MEMORY.md）
    
    Args:
        project_path: 项目路径
    
    Returns:
        索引文件路径
    """
    return os.path.join(get_auto_memory_dir(project_path), ENTRYPOINT_NAME)


def get_memory_file_path(project_path: str, memory_name: str) -> str:
    """
    获取记忆文件路径
    
    Args:
        project_path: 项目路径
        memory_name: 记忆名称
    
    Returns:
        记忆文件路径
    """
    # 确保文件名以 .md 结尾
    if not memory_name.endswith('.md'):
        memory_name = memory_name + '.md'
    
    return os.path.join(get_auto_memory_dir(project_path), memory_name)


def ensure_memory_dir_exists(project_path: str) -> str:
    """
    确保记忆目录存在
    
    Args:
        project_path: 项目路径
    
    Returns:
        记忆目录路径
    """
    memory_dir = get_auto_memory_dir(project_path)
    os.makedirs(memory_dir, exist_ok=True)
    return memory_dir


def is_auto_memory_path(file_path: str, project_path: str) -> bool:
    """
    检查文件路径是否在 Auto Memory 目录内
    
    Args:
        file_path: 文件路径
        project_path: 项目路径
    
    Returns:
        是否在记忆目录内
    """
    memory_dir = os.path.normpath(get_auto_memory_dir(project_path))
    normalized_file = os.path.normpath(file_path)
    return normalized_file.startswith(memory_dir)


# ============================================================================
# Agent Memory 路径
# ============================================================================

def get_agent_memory_base_dir() -> str:
    """
    获取 Agent Memory 基础目录
    
    Returns:
        Agent Memory 基础目录路径
    """
    return os.path.join(get_memory_base_dir(), 'agent-memory')


def get_agent_memory_dir(
    agent_type: str,
    scope: str,
    project_path: Optional[str] = None
) -> str:
    """
    获取 Agent Memory 目录路径
    
    Args:
        agent_type: Agent 类型
        scope: 作用域 ('user', 'project', 'local')
        project_path: 项目路径（project/local 作用域需要）
    
    Returns:
        Agent Memory 目录路径
    """
    # 规范化 agent 类型（替换冒号）
    safe_agent_type = agent_type.replace(':', '-')
    
    if scope == 'user':
        # 用户级：~/.claude/agent-memory/<agent-type>/
        return os.path.join(get_agent_memory_base_dir(), safe_agent_type) + os.sep
    
    elif scope == 'project':
        # 项目级：<project>/.claude/agent-memory/<agent-type>/
        if not project_path:
            raise ValueError("Project path required for 'project' scope")
        return os.path.join(project_path, '.claude', 'agent-memory', safe_agent_type) + os.sep
    
    elif scope == 'local':
        # 本地级：<project>/.claude/agent-memory-local/<agent-type>/
        if not project_path:
            raise ValueError("Project path required for 'local' scope")
        
        # 检查远程记忆目录
        remote_dir = os.environ.get('CLAUDE_CODE_REMOTE_MEMORY_DIR')
        if remote_dir:
            # 远程模式：使用项目命名空间
            return os.path.join(
                remote_dir, 'projects', sanitize_path(project_path),
                'agent-memory-local', safe_agent_type
            ) + os.sep
        
        return os.path.join(project_path, '.claude', 'agent-memory-local', safe_agent_type) + os.sep
    
    else:
        raise ValueError(f"Invalid scope: {scope}")


def get_agent_memory_file(
    agent_type: str,
    scope: str,
    project_path: Optional[str] = None
) -> str:
    """
    获取 Agent Memory 文件路径
    
    Args:
        agent_type: Agent 类型
        scope: 作用域
        project_path: 项目路径
    
    Returns:
        Agent Memory 文件路径
    """
    return os.path.join(
        get_agent_memory_dir(agent_type, scope, project_path),
        ENTRYPOINT_NAME
    )


def is_agent_memory_path(file_path: str, project_path: Optional[str] = None) -> bool:
    """
    检查文件路径是否在任何 Agent Memory 目录内
    
    Args:
        file_path: 文件路径
        project_path: 项目路径
    
    Returns:
        是否在 Agent Memory 目录内
    """
    normalized = os.path.normpath(file_path)
    
    # 检查用户级
    user_base = os.path.normpath(get_agent_memory_base_dir())
    if normalized.startswith(user_base + os.sep):
        return True
    
    # 检查项目级和本地级
    if project_path:
        project_agent_dir = os.path.normpath(
            os.path.join(project_path, '.claude', 'agent-memory')
        )
        project_local_dir = os.path.normpath(
            os.path.join(project_path, '.claude', 'agent-memory-local')
        )
        
        if normalized.startswith(project_agent_dir + os.sep):
            return True
        if normalized.startswith(project_local_dir + os.sep):
            return True
    
    return False


# ============================================================================
# 辅助函数
# ============================================================================

def get_memory_scope_display(scope: Optional[str]) -> str:
    """
    获取记忆作用域的显示名称
    
    Args:
        scope: 作用域
    
    Returns:
        显示名称
    """
    if scope == 'user':
        return f"User ({get_agent_memory_base_dir()}/)"
    elif scope == 'project':
        return "Project (.claude/agent-memory/)"
    elif scope == 'local':
        return "Local (.claude/agent-memory-local/)"
    else:
        return "None"


def get_git_root(project_path: str) -> Optional[str]:
    """
    获取 Git 仓库根目录
    
    用于让同一仓库的所有 worktree 共享记忆目录
    
    Args:
        project_path: 项目路径
    
    Returns:
        Git 根目录，或 None
    """
    import subprocess
    
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    
    return None


def get_canonical_project_path(project_path: str) -> str:
    """
    获取规范化的项目路径
    
    使用 Git 根目录（如果存在），否则使用原路径
    这确保同一仓库的所有 worktree 共享记忆
    
    Args:
        project_path: 项目路径
    
    Returns:
        规范化的项目路径
    """
    git_root = get_git_root(project_path)
    if git_root:
        return git_root
    return project_path
