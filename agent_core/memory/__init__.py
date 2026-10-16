"""
Memory System - 记忆系统

自动记忆机制：
- Auto Memory: 跨会话持久化记忆（用户画像、反馈、项目上下文、外部引用）
- Agent Memory: Agent 级别记忆（为特定 Agent 类型提供独立知识存储）

存储结构：
~/.pebble/projects/<project-hash>/memory/
├── MEMORY.md          # 索引文件（最多200行，25KB）
├── user_*.md          # 用户画像记忆
├── feedback_*.md      # 反馈记忆
├── project_*.md       # 项目记忆
└── reference_*.md     # 引用记忆

Agent Memory:
~/.pebble/agent-memory/<agent-type>/MEMORY.md     # 用户级
<project>/.pebble/agent-memory/<agent-type>/      # 项目级
<project>/.pebble/agent-memory-local/<agent-type>/ # 本地级
"""

from .auto_memory_types import (
    MemoryType,
    MemoryEntry,
    MemoryIndex,
    MEMORY_TYPES,
    MEMORY_FRONTMATTER_EXAMPLE,
    TYPES_SECTION_COMBINED,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
    TRUSTING_RECALL_SECTION,
)

from .auto_memory_paths import (
    get_config_home,
    get_projects_dir,
    sanitize_path,
    get_project_dir,
    get_auto_memory_dir,
    get_memory_index_path,
    get_memory_file_path,
    ensure_memory_dir_exists,
    is_auto_memory_path,
)

from .auto_memory_scan import (
    scan_memory_files,
    format_memory_manifest,
    find_relevant_memories,
)

from .auto_memory import (
    AutoMemoryManager,
    get_auto_memory_manager,
)

from .auto_memory_extractor import (
    AutoMemoryExtractor,
    ExtractMemoryConfig,
)

from .agent_memory import (
    AgentMemoryScope,
    AgentMemoryManager,
    get_agent_memory_manager,
)

from .memory_prompt import (
    build_memory_prompt,
    build_memory_lines,
    build_agent_memory_prompt,
    load_memory_prompt,
    get_memory_prompt_for_agent,
    get_combined_memory_prompt,
)

__all__ = [
    # 记忆类型
    'MemoryType',
    'MemoryEntry',
    'MemoryIndex',
    'MEMORY_TYPES',
    'MEMORY_FRONTMATTER_EXAMPLE',
    'TYPES_SECTION_COMBINED',
    'TYPES_SECTION_INDIVIDUAL',
    'WHAT_NOT_TO_SAVE_SECTION',
    'WHEN_TO_ACCESS_SECTION',
    'TRUSTING_RECALL_SECTION',
    
    # 路径管理
    'get_config_home',
    'get_projects_dir',
    'sanitize_path',
    'get_project_dir',
    'get_auto_memory_dir',
    'get_memory_index_path',
    'get_memory_file_path',
    'ensure_memory_dir_exists',
    'is_auto_memory_path',
    
    # 记忆扫描
    'scan_memory_files',
    'format_memory_manifest',
    'find_relevant_memories',
    
    # Auto Memory
    'AutoMemoryManager',
    'get_auto_memory_manager',
    
    # 记忆提取
    'AutoMemoryExtractor',
    'ExtractMemoryConfig',
    
    # Agent Memory
    'AgentMemoryScope',
    'AgentMemoryManager',
    'get_agent_memory_manager',
    
    # 提示词构建
    'build_memory_prompt',
    'build_memory_lines',
    'build_agent_memory_prompt',
    'load_memory_prompt',
    'get_memory_prompt_for_agent',
    'get_combined_memory_prompt',
]
