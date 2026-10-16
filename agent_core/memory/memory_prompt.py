"""
Memory Prompt - 记忆提示词构建

1. Auto Memory 提示词构建
2. Agent Memory 提示词构建
3. 记忆内容加载和截断
4. 搜索历史上下文指导

用于：
- 注入到系统提示词
- 记忆提取时的指导
- Agent 初始化时的记忆加载
"""

import os
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path

from .auto_memory_types import (
    MemoryType,
    MemoryEntry,
    MemoryIndex,
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_LINES,
    MAX_ENTRYPOINT_BYTES,
    TYPES_SECTION_INDIVIDUAL,
    TYPES_SECTION_COMBINED,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
    TRUSTING_RECALL_SECTION,
    MEMORY_FRONTMATTER_EXAMPLE,
    truncate_entrypoint_content,
)
from .auto_memory_paths import (
    get_auto_memory_dir,
    get_memory_index_path,
    ensure_memory_dir_exists,
)
from .auto_memory_scan import scan_memory_files, format_memory_manifest
from .auto_memory import AutoMemoryManager, get_auto_memory_manager
from .agent_memory import AgentMemoryManager, AgentMemoryScope, get_agent_memory_manager

logger = logging.getLogger(__name__)


# ============================================================================
# 常量
# ============================================================================

# 目录存在指导
DIR_EXISTS_GUIDANCE = '此目录已存在 — 写入时直接使用 Write 工具（不要运行 mkdir 或检查是否存在）。'
DIRS_EXIST_GUIDANCE = '两个目录都已存在 — 写入时直接使用 Write 工具（不要运行 mkdir 或检查是否存在）。'


# ============================================================================
# Auto Memory 提示词构建
# ============================================================================

def build_memory_lines(
    display_name: str,
    memory_dir: str,
    extra_guidelines: Optional[List[str]] = None,
    skip_index: bool = False
) -> List[str]:
    """
    构建记忆行为指导行（不含 MEMORY.md 内容）
    
    用于：
    - build_memory_prompt（包含内容）
    - load_memory_prompt（内容通过 user context 注入）
    
    Args:
        display_name: 显示名称
        memory_dir: 记忆目录路径
        extra_guidelines: 额外指导
        skip_index: 是否跳过索引指导
    
    Returns:
        提示词行列表
    """
    # 如何保存部分
    if skip_index:
        how_to_save = [
            '## 如何保存记忆',
            '',
            '将每条记忆写入单独的文件（如 `user_role.md`, `feedback_testing.md`），使用以下 frontmatter 格式：',
            '',
            *MEMORY_FRONTMATTER_EXAMPLE,
            '',
            '- 保持记忆文件中的 name、description 和 type 字段与内容同步更新',
            '- 按主题语义组织记忆，而非按时间顺序',
            '- 更新或移除错误或过时的记忆',
            '- 不要写入重复的记忆。先检查是否有可以更新的现有记忆，再写入新记忆。',
        ]
    else:
        how_to_save = [
            '## 如何保存记忆',
            '',
            '保存记忆是一个两步过程：',
            '',
            '**步骤 1** — 将记忆写入单独的文件（如 `user_role.md`, `feedback_testing.md`），使用以下 frontmatter 格式：',
            '',
            *MEMORY_FRONTMATTER_EXAMPLE,
            '',
            f'**步骤 2** — 在 `{ENTRYPOINT_NAME}` 中添加指向该文件的指针。`{ENTRYPOINT_NAME}` 是索引，不是记忆 — 每条记录应为一行，约 150 字符：`- [标题](文件.md) — 一行钩子`。它没有 frontmatter。永远不要将记忆内容直接写入 `{ENTRYPOINT_NAME}`。',
            '',
            f'- `{ENTRYPOINT_NAME}` 始终加载到对话上下文中 — {MAX_ENTRYPOINT_LINES} 行后的内容会被截断，所以保持索引简洁',
            '- 保持记忆文件中的 name、description 和 type 字段与内容同步更新',
            '- 按主题语义组织记忆，而非按时间顺序',
            '- 更新或移除错误或过时的记忆',
            '- 不要写入重复的记忆。先检查是否有可以更新的现有记忆，再写入新记忆。',
        ]
    
    lines: List[str] = [
        f'# {display_name}',
        '',
        f'你有一个持久化的、基于文件的记忆系统，位于 `{memory_dir}`。{DIR_EXISTS_GUIDANCE}',
        '',
        '你应该随着时间建立这个记忆系统，以便未来的对话能够完整了解：',
        '- 用户是谁',
        '- 用户希望如何与你协作',
        '- 应该避免或重复哪些行为',
        '- 用户给你的工作的上下文',
        '',
        '如果用户明确要求你记住某些内容，立即保存为最合适的类型。如果用户要求你忘记某些内容，找到并移除相关条目。',
        '',
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        '',
        *how_to_save,
        '',
        *WHEN_TO_ACCESS_SECTION,
        '',
        *TRUSTING_RECALL_SECTION,
        '',
        '## 记忆与其他持久化形式',
        '记忆是你可以使用的几种持久化机制之一。区别在于记忆可以在未来对话中回忆，不应用于持久化仅在当前对话中有用的信息。',
        '- 何时使用或更新计划而非记忆：如果你即将开始非平凡的实现任务，并希望与用户对齐方法，应使用计划而非保存到记忆。同样，如果对话中已有计划且方法已更改，通过更新计划来持久化该更改而非保存记忆。',
        '- 何时使用或更新任务而非记忆：当你需要将当前对话中的工作分解为离散步骤或跟踪进度时，使用任务而非保存到记忆。任务非常适合持久化当前对话中需要完成的工作信息，但记忆应保留对未来对话有用的信息。',
        '',
        *(extra_guidelines or []),
        '',
    ]
    
    # 添加搜索历史上下文指导
    lines.extend(_build_searching_past_context_section(memory_dir))
    
    return lines


def _build_searching_past_context_section(memory_dir: str) -> List[str]:
    """构建搜索历史上下文部分"""
    return [
        '## 搜索过去的上下文',
        '',
        '查找过去的上下文时：',
        f'1. 搜索记忆目录中的主题文件：',
        '   ```',
        f'   grep -rn "<搜索词>" {memory_dir} --include="*.md"',
        '   ```',
        '2. 会话转录日志（最后手段 — 大文件，慢）：',
        '   ```',
        '   grep -rn "<搜索词>" <项目目录>/ --include="*.jsonl"',
        '   ```',
        '使用窄搜索词（错误消息、文件路径、函数名）而非宽泛关键词。',
        '',
    ]


def build_memory_prompt(params: Dict[str, Any]) -> str:
    """
    构建完整的记忆提示词（包含 MEMORY.md 内容）
    
    用于 agent memory（不依赖项目文档扫描）
    
    Args:
        params: {
            'display_name': str,
            'memory_dir': str,
            'extra_guidelines': Optional[List[str]]
        }
    
    Returns:
        完整的记忆提示词
    """
    display_name = params.get('display_name', 'Auto Memory')
    memory_dir = params.get('memory_dir', '')
    extra_guidelines = params.get('extra_guidelines')
    
    fs = params.get('fs')  # 可选的文件系统实现
    
    # 读取入口文件
    entrypoint_path = os.path.join(memory_dir, ENTRYPOINT_NAME)
    entrypoint_content = ''
    
    try:
        if fs and hasattr(fs, 'readFileSync'):
            entrypoint_content = fs.readFileSync(entrypoint_path, {'encoding': 'utf-8'})
        elif os.path.exists(entrypoint_path):
            with open(entrypoint_path, 'r', encoding='utf-8') as f:
                entrypoint_content = f.read()
    except Exception:
        pass
    
    # 构建基础行
    lines = build_memory_lines(display_name, memory_dir, extra_guidelines)
    
    # 添加入口文件内容
    if entrypoint_content.strip():
        truncated = truncate_entrypoint_content(entrypoint_content)
        content, line_count, byte_count, was_line_truncated, was_byte_truncated = truncated
        
        if was_line_truncated or was_byte_truncated:
            reason = []
            if was_byte_truncated and not was_line_truncated:
                reason.append(f'{byte_count} 字节（限制: {MAX_ENTRYPOINT_BYTES}）')
            elif was_line_truncated and not was_byte_truncated:
                reason.append(f'{line_count} 行（限制: {MAX_ENTRYPOINT_LINES}）')
            else:
                reason.append(f'{line_count} 行和 {byte_count} 字节')
            
            content += f"\n\n> 警告: {ENTRYPOINT_NAME} 已 {', '.join(reason)}。只加载了部分内容。保持索引条目在一行约 200 字符内；将详情移入主题文件。"
        
        lines.append(f'## {ENTRYPOINT_NAME}')
        lines.append('')
        lines.append(content)
    else:
        lines.append(f'## {ENTRYPOINT_NAME}')
        lines.append('')
        lines.append(f'你的 {ENTRYPOINT_NAME} 当前为空。当你保存新记忆时，它们会出现在这里。')
    
    return '\n'.join(lines)


def load_memory_prompt() -> Optional[str]:
    """
    加载统一的记忆提示词（用于注入到系统提示词）
    
    分发逻辑：
    - auto + team: 组合提示词（两个目录）
    - auto only: 记忆行（单个目录）
    
    Returns:
        记忆提示词，或 None（禁用时）
    """
    manager = get_auto_memory_manager()
    
    project_path = manager.get_project_path()
    if not project_path:
        return None
    
    # 检查是否启用
    if manager.is_persistence_disabled():
        return None
    
    # 获取记忆目录
    memory_dir = get_auto_memory_dir(project_path)
    
    # 确保目录存在
    ensure_memory_dir_exists(project_path)
    
    # 检查是否有实际的记忆文件内容
    # 如果没有任何记忆文件，跳过整个记忆提示词注入
    has_actual_memory = False
    index_path = get_memory_index_path(project_path)
    
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            index_content = f.read()
        if index_content.strip():
            has_actual_memory = True
    
    # 检查 memory_dir 中是否有真实的记忆文件 (除 MEMORY.md 外的 .md 文件)
    if not has_actual_memory and os.path.isdir(memory_dir):
        for fname in os.listdir(memory_dir):
            if fname.endswith('.md') and fname != 'MEMORY.md':
                filepath = os.path.join(memory_dir, fname)
                if os.path.isfile(filepath) and os.path.getsize(filepath) > 0:
                    has_actual_memory = True
                    break
    
    if not has_actual_memory:
        return None
    
    # 构建提示词
    lines = build_memory_lines('Auto Memory', memory_dir)
    
    if has_actual_memory and os.path.exists(index_path):
        try:
            truncated = truncate_entrypoint_content(index_content)
            content, _, _, was_line_truncated, was_byte_truncated = truncated
            
            if was_line_truncated or was_byte_truncated:
                content += f"\n\n> 警告: {ENTRYPOINT_NAME} 已被截断。保持索引条目简洁。"
            
            lines.append(f'## {ENTRYPOINT_NAME}')
            lines.append('')
            lines.append(content)
        except Exception as e:
            logger.error(f"[MemoryPrompt] Failed to load index: {e}")
    
    return '\n'.join(lines)


# ============================================================================
# Agent Memory 提示词构建
# ============================================================================

def build_agent_memory_prompt(
    agent_type: str,
    scope: AgentMemoryScope,
    extra_guidelines: Optional[List[str]] = None
) -> str:
    """
    构建 Agent 记忆提示词
    
    Args:
        agent_type: Agent 类型
        scope: 作用域
        extra_guidelines: 额外指导
    
    Returns:
        Agent 记忆提示词
    """
    manager = get_agent_memory_manager()
    
    memory_dir = manager.get_memory_dir(agent_type, scope)
    memory_content = manager.read_memory(agent_type, scope)
    
    # 作用域说明
    scope_notes = {
        AgentMemoryScope.USER: '- 由于此记忆是用户级作用域，保持学习内容的通用性，因为它们适用于所有项目',
        AgentMemoryScope.PROJECT: '- 由于此记忆是项目级作用域并通过版本控制与团队共享，为该项目定制记忆',
        AgentMemoryScope.LOCAL: '- 由于此记忆是本地作用域（不检入版本控制），为该项目和机器定制记忆',
    }
    
    # Cowork 额外指导
    cowork_extra = os.environ.get('PEBBLE_MEMORY_EXTRA_GUIDELINES', '')
    
    all_extra = []
    if scope_notes.get(scope):
        all_extra.append(scope_notes[scope])
    if cowork_extra.strip():
        all_extra.append(cowork_extra)
    if extra_guidelines:
        all_extra.extend(extra_guidelines)
    
    # 确保目录存在
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except Exception:
        pass
    
    # 没有实际记忆内容时，跳过整个记忆提示词注入
    # 避免空壳说明干扰模型对对话上下文的注意力
    if not memory_content:
        return ''
    
    # 构建提示词
    lines = [
        '# Persistent Agent Memory',
        '',
        f'你有一个持久化的、基于文件的记忆系统，位于: `{memory_dir}`',
        '',
        '此会话是长寿命的。工作时，通过 **追加** 到今天的每日日志文件来记录任何值得记住的内容：',
        '',
        f'`{memory_dir}MEMORY.md`',
        '',
        '直接写入 MEMORY.md 文件（使用 Write 工具）。',
        '',
        '## 记忆类型',
        '',
        '### user',
        '包含关于用户角色、目标、职责和知识的信息。',
        '',
        '### feedback',
        '用户给你的关于如何工作的指导——包括应该避免什么和应该继续做什么。',
        '',
        '### project',
        '你了解到的关于项目中正在进行的工作、目标、计划、bug 或事件的信息。',
        '',
        '### reference',
        '指向外部系统中信息位置的指针。',
        '',
        *WHAT_NOT_TO_SAVE_SECTION,
        '',
        '## 记忆内容',
        '',
    ]
    
    lines.append(memory_content)
    
    if all_extra:
        lines.append('')
        lines.extend(all_extra)
    
    return '\n'.join(lines)


# ============================================================================
# 便捷函数
# ============================================================================

def get_memory_prompt_for_agent(
    agent_type: str,
    project_path: Optional[str] = None
) -> str:
    """
    获取 Agent 的记忆提示词
    
    自动检测 Agent 的记忆配置并构建相应提示词
    
    Args:
        agent_type: Agent 类型
        project_path: 项目路径
    
    Returns:
        记忆提示词
    """
    agent_manager = get_agent_memory_manager()
    
    if project_path:
        agent_manager.set_project(project_path)
    
    # 默认使用 project 作用域
    return build_agent_memory_prompt(agent_type, AgentMemoryScope.PROJECT)


def get_combined_memory_prompt(
    project_path: str,
    agent_type: Optional[str] = None
) -> str:
    """
    获取组合记忆提示词（Auto Memory + Agent Memory）
    
    Args:
        project_path: 项目路径
        agent_type: 可选的 Agent 类型
    
    Returns:
        组合的记忆提示词
    """
    parts = []
    
    # Auto Memory
    auto_manager = get_auto_memory_manager()
    auto_manager.set_project(project_path)
    
    auto_prompt = load_memory_prompt()
    if auto_prompt:
        parts.append(auto_prompt)
    
    # Agent Memory
    if agent_type:
        agent_prompt = get_memory_prompt_for_agent(agent_type, project_path)
        if agent_prompt:
            parts.append('---')
            parts.append(agent_prompt)
    
    return '\n\n'.join(parts)
