"""
Auto Memory Types - 记忆类型定义

1. 四种记忆类型（user, feedback, project, reference）
2. 记忆条目数据结构
3. 记忆索引结构
4. 提示词模板

记忆类型约束：
- 只存储不可从当前项目状态推导的信息
- 代码模式、架构、git历史等可推导信息不应保存
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from enum import Enum
import json
import re
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# 常量定义
# ============================================================================

# 记忆入口文件名
ENTRYPOINT_NAME = 'MEMORY.md'

# 索引文件限制
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000  # 25KB

# 四种记忆类型
MEMORY_TYPES = ['user', 'feedback', 'project', 'reference']


class MemoryType(Enum):
    """记忆类型枚举"""
    USER = 'user'           # 用户画像
    FEEDBACK = 'feedback'   # 行为指导
    PROJECT = 'project'     # 项目上下文
    REFERENCE = 'reference' # 外部引用


def parse_memory_type(raw: Any) -> Optional[MemoryType]:
    """
    解析记忆类型
    
    Args:
        raw: 原始值（通常来自 frontmatter）
    
    Returns:
        MemoryType 或 None（无效值）
    """
    if isinstance(raw, MemoryType):
        return raw
    if isinstance(raw, str):
        try:
            return MemoryType(raw.lower())
        except ValueError:
            return None
    return None


# ============================================================================
# 记忆条目数据结构
# ============================================================================

@dataclass
class MemoryEntry:
    """
    记忆条目
    
    存储为 Markdown 文件，使用 YAML frontmatter：
    ```markdown
    ---
    name: memory_name
    description: 一行描述
    type: user|feedback|project|reference
    created_at: 2026-01-01T00:00:00
    updated_at: 2026-01-01T00:00:00
    tags: [tag1, tag2]
    ---
    
    记忆内容...
    ```
    """
    name: str
    memory_type: MemoryType
    description: str
    content: str
    file_path: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_frontmatter(self) -> str:
        """
        转换为 YAML frontmatter 格式的 Markdown
        
        Returns:
            完整的 Markdown 内容
        """
        tags_str = json.dumps(self.tags, ensure_ascii=False) if self.tags else '[]'
        
        return f"""---
name: {self.name}
description: {self.description}
type: {self.memory_type.value}
created_at: {self.created_at}
updated_at: {self.updated_at}
tags: {tags_str}
---

{self.content}"""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'name': self.name,
            'memory_type': self.memory_type.value,
            'description': self.description,
            'content': self.content,
            'file_path': self.file_path,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'tags': self.tags,
            'metadata': self.metadata,
        }
    
    @classmethod
    def from_frontmatter(cls, content: str, file_path: Optional[str] = None) -> Optional['MemoryEntry']:
        """
        从 Markdown frontmatter 解析
        
        Args:
            content: 文件内容
            file_path: 文件路径
        
        Returns:
            MemoryEntry 或 None（解析失败）
        """
        # 匹配 frontmatter
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
        if not match:
            logger.warning(f"[MemoryEntry] No frontmatter found in content")
            return None
        
        frontmatter_str, body = match.groups()
        
        # 解析 frontmatter
        props = {}
        current_key = None
        current_value = []
        
        for line in frontmatter_str.split('\n'):
            # 检查是否是新键值对
            if ':' in line and not line.startswith(' ') and not line.startswith('\t'):
                # 保存之前的键值对
                if current_key:
                    props[current_key] = '\n'.join(current_value).strip()
                
                # 解析新键值对
                key, value = line.split(':', 1)
                current_key = key.strip()
                current_value = [value.strip()]
            elif current_key:
                # 续行
                current_value.append(line)
        
        # 保存最后一个键值对
        if current_key:
            props[current_key] = '\n'.join(current_value).strip()
        
        # 解析类型
        memory_type = parse_memory_type(props.get('type'))
        if memory_type is None:
            logger.warning(f"[MemoryEntry] Invalid memory type: {props.get('type')}")
            memory_type = MemoryType.PROJECT  # 默认类型
        
        # 解析 tags
        tags = []
        tags_str = props.get('tags', '[]')
        try:
            if tags_str.startswith('['):
                tags = json.loads(tags_str)
            elif tags_str:
                tags = [t.strip() for t in tags_str.split(',')]
        except:
            pass
        
        return cls(
            name=props.get('name', 'unnamed'),
            memory_type=memory_type,
            description=props.get('description', ''),
            content=body.strip(),
            file_path=file_path,
            created_at=props.get('created_at', datetime.now().isoformat()),
            updated_at=props.get('updated_at', datetime.now().isoformat()),
            tags=tags,
        )
    
    def get_hook_line(self) -> str:
        """获取索引中的钩子行"""
        return f"- [{self.name}]({self.name}.md) — {self.description}"


# ============================================================================
# 记忆索引
# ============================================================================

@dataclass
class MemoryIndex:
    """
    记忆索引（MEMORY.md 内容）
    
    索引文件是一个简单的 Markdown 列表，每条记忆一行：
    ```markdown
    # Memory Index
    
    - [user_role](user_role.md) — 用户是数据科学家
    - [feedback_testing](feedback_testing.md) — 不要 mock 数据库
    ```
    """
    entries: List[Dict[str, str]] = field(default_factory=list)
    
    def to_markdown(self) -> str:
        """
        转换为 Markdown 格式
        
        Returns:
            Markdown 内容
        """
        lines = [
            "# Memory Index",
            "",
            "<!-- 此文件由系统自动维护，请勿手动编辑 -->",
            "",
        ]
        
        if not self.entries:
            lines.append("（暂无记忆）")
        else:
            for entry in self.entries:
                title = entry.get('title', entry.get('name', 'Untitled'))
                path = entry.get('path', f"{title}.md")
                hook = entry.get('hook', entry.get('description', ''))
                lines.append(f"- [{title}]({path}) — {hook}")
        
        return "\n".join(lines)
    
    def add_entry(self, name: str, description: str, path: Optional[str] = None) -> None:
        """
        添加索引条目
        
        Args:
            name: 记忆名称
            description: 描述
            path: 文件路径（默认为 {name}.md）
        """
        if path is None:
            path = f"{name}.md"
        
        # 检查是否已存在
        for i, entry in enumerate(self.entries):
            if entry.get('name') == name or entry.get('path') == path:
                # 更新现有条目
                self.entries[i] = {
                    'name': name,
                    'title': name,
                    'path': path,
                    'hook': description,
                    'description': description,
                }
                return
        
        # 添加新条目
        self.entries.append({
            'name': name,
            'title': name,
            'path': path,
            'hook': description,
            'description': description,
        })
    
    def remove_entry(self, name: str) -> bool:
        """
        移除索引条目
        
        Args:
            name: 记忆名称
        
        Returns:
            是否成功移除
        """
        original_len = len(self.entries)
        self.entries = [e for e in self.entries if e.get('name') != name]
        return len(self.entries) < original_len
    
    def get_entry(self, name: str) -> Optional[Dict[str, str]]:
        """获取索引条目"""
        for entry in self.entries:
            if entry.get('name') == name:
                return entry
        return None
    
    @classmethod
    def from_markdown(cls, content: str) -> 'MemoryIndex':
        """
        从 Markdown 解析索引
        
        Args:
            content: Markdown 内容
        
        Returns:
            MemoryIndex 实例
        """
        entries = []
        
        # 匹配列表项: - [title](path) — hook
        pattern = r'-\s*\[([^\]]+)\]\(([^)]+)\)\s*[—-]\s*(.+)'
        
        for match in re.finditer(pattern, content):
            title = match.group(1).strip()
            path = match.group(2).strip()
            hook = match.group(3).strip()
            
            # 从路径提取名称
            name = path.replace('.md', '')
            
            entries.append({
                'name': name,
                'title': title,
                'path': path,
                'hook': hook,
                'description': hook,
            })
        
        return cls(entries=entries)


# ============================================================================
# 提示词模板
# ============================================================================

# Frontmatter 格式示例
MEMORY_FRONTMATTER_EXAMPLE: Tuple[str, ...] = (
    '```markdown',
    '---',
    'name: {{memory name}}',
    'description: {{one-line description — used to decide relevance in future conversations, so be specific}}',
    f'type: {{{"|".join(MEMORY_TYPES)}}}',
    '---',
    '',
    '{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}',
    '```',
)

# 记忆类型说明（组合模式 - 包含 team/private）
TYPES_SECTION_COMBINED: Tuple[str, ...] = (
    '## Types of memory',
    '',
    'There are several discrete types of memory that you can store in your memory system. Each type below declares a <scope> of `private`, `team`, or guidance for choosing between the two.',
    '',
    '<types>',
    '<type>',
    '    <name>user</name>',
    '    <scope>always private</scope>',
    "    <description>Contain information about the user's role, goals, responsibilities, and knowledge. Great user memories help you tailor your future behavior to the user's preferences and perspective. Your goal in reading and writing these memories is to build up an understanding of who the user is and how you can be most helpful to them specifically. For example, you should collaborate with a senior software engineer differently than a student who is coding for the very first time. Keep in mind, that the aim here is to be helpful to the user. Avoid writing memories about the user that could be viewed as a negative judgement or that are not relevant to the work you're trying to accomplish together.</description>",
    "    <when_to_save>When you learn any details about the user's role, preferences, responsibilities, or knowledge</when_to_save>",
    "    <how_to_use>When your work should be informed by the user's profile or perspective. For example, if the user is asking you to explain a part of the code, you should answer that question in a way that is tailored to the specific details that they will find most valuable or that helps them build their mental model in relation to domain knowledge they already have.</how_to_use>",
    '    <examples>',
    "    user: I'm a data scientist investigating what logging we have in place",
    '    assistant: [saves private user memory: user is a data scientist, currently focused on observability/logging]',
    '',
    "    user: I've been writing Go for ten years but this is my first time touching the React side of this repo",
    "    assistant: [saves private user memory: deep Go expertise, new to React and this project's frontend — frame frontend explanations in terms of backend analogues]",
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>feedback</name>',
    '    <scope>default to private. Save as team only when the guidance is clearly a project-wide convention that every contributor should follow (e.g., a testing policy, a build invariant), not a personal style preference.</scope>',
    "    <description>Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. These are a very important type of memory to read and write as they allow you to remain coherent and responsive to the way you should approach work in the project. Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious. Before saving a private feedback memory, check that it doesn't contradict a team feedback memory — if it does, either don't save it or note the override explicitly.</description>",
    '    <when_to_save>Any time the user corrects your approach ("no not that", "don\'t", "stop doing X") OR confirms a non-obvious approach worked ("yes exactly", "perfect, keep doing that", accepting an unusual choice without pushback). Corrections are easy to notice; confirmations are quieter — watch for them. In both cases, save what is applicable to future conversations, especially if surprising or not obvious from the code. Include *why* so you can judge edge cases later.</when_to_save>',
    '    <how_to_use>Let these memories guide your behavior so that the user and other users in the project do not need to offer the same guidance twice.</how_to_use>',
    '    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user gave — often a past incident or strong preference) and a **How to apply:** line (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead of blindly following the rule.</body_structure>',
    '    <examples>',
    "    user: don't mock the database in these tests — we got burned last quarter when mocked tests passed but the prod migration failed",
    '    assistant: [saves team feedback memory: integration tests must hit a real database, not mocks. Reason: prior incident where mock/prod divergence masked a broken migration. Team scope: this is a project testing policy, not a personal preference]',
    '',
    '    user: stop summarizing what you just did at the end of every response, I can read the diff',
    "    assistant: [saves private feedback memory: this user wants terse responses with no trailing summaries. Private because it's a communication preference, not a project convention]",
    '',
    "    user: yeah the single bundled PR was the right call here, splitting this one would've just been churn",
    '    assistant: [saves private feedback memory: for refactors in this area, user prefers one bundled PR over many small ones. Confirmed after I chose this approach — a validated judgment call, not a correction]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>project</name>',
    '    <scope>private or team, but strongly bias toward team</scope>',
    '    <description>Information that you learn about ongoing work, goals, initiatives, bugs, or incidents within the project that is not otherwise derivable from the code or git history. Project memories help you understand the broader context and motivation behind the work users are working on within this working directory.</description>',
    '    <when_to_save>When you learn who is doing what, why, or by when. These states change relatively quickly so try to keep your understanding of this up to date. Always convert relative dates in user messages to absolute dates when saving (e.g., "Thursday" → "2026-03-05"), so the memory remains interpretable after time passes.</when_to_save>',
    "    <how_to_use>Use these memories to more fully understand the details and nuance behind the user's request, anticipate coordination issues across users, make better informed suggestions.</how_to_use>",
    '    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation — often a constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this should shape your suggestions). Project memories decay fast, so the why helps future-you judge whether the memory is still load-bearing.</body_structure>',
    '    <examples>',
    "    user: we're freezing all non-critical merges after Thursday — mobile team is cutting a release branch",
    '    assistant: [saves team project memory: merge freeze begins 2026-03-05 for mobile release cut. Flag any non-critical PR work scheduled after that date]',
    '',
    "    user: the reason we're ripping out the old auth middleware is that legal flagged it for storing session tokens in a way that doesn't meet the new compliance requirements",
    '    assistant: [saves team project memory: auth middleware rewrite is driven by legal/compliance requirements around session token storage, not tech-debt cleanup — scope decisions should favor compliance over ergonomics]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>reference</name>',
    '    <scope>usually team</scope>',
    '    <description>Stores pointers to where information can be found in external systems. These memories allow you to remember where to look to find up-to-date information outside of the project directory.</description>',
    '    <when_to_save>When you learn about resources in external systems and their purpose. For example, that bugs are tracked in a specific project in Linear or that feedback can be found in a specific Slack channel.</when_to_save>',
    '    <how_to_use>When the user references an external system or information that may be in an external system.</how_to_use>',
    '    <examples>',
    '    user: check the Linear project "INGEST" if you want context on these tickets, that\'s where we track all pipeline bugs',
    '    assistant: [saves team reference memory: pipeline bugs are tracked in Linear project "INGEST"]',
    '',
    "    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches — if you're touching request handling, that's the thing that'll page someone",
    '    assistant: [saves team reference memory: grafana.internal/d/api-latency is the oncall latency dashboard — check it when editing request-path code]',
    '    </examples>',
    '</type>',
    '</types>',
)

# 记忆类型说明（独立模式 - 无 team/private 区分）
TYPES_SECTION_INDIVIDUAL: Tuple[str, ...] = (
    '## Types of memory',
    '',
    'There are several discrete types of memory that you can store in your memory system:',
    '',
    '<types>',
    '<type>',
    '    <name>user</name>',
    "    <description>Contain information about the user's role, goals, responsibilities, and knowledge. Great user memories help you tailor your future behavior to the user's preferences and perspective. Your goal in reading and writing these memories is to build up an understanding of who the user is and how you can be most helpful to them specifically. For example, you should collaborate with a senior software engineer differently than a student who is coding for the very first time. Keep in mind, that the aim here is to be helpful to the user. Avoid writing memories about the user that could be viewed as a negative judgement or that are not relevant to the work you're trying to accomplish together.</description>",
    "    <when_to_save>When you learn any details about the user's role, preferences, responsibilities, or knowledge</when_to_save>",
    "    <how_to_use>When your work should be informed by the user's profile or perspective. For example, if the user is asking you to explain a part of the code, you should answer that question in a way that is tailored to the specific details that they will find most valuable or that helps them build their mental model in relation to domain knowledge they already have.</how_to_use>",
    '    <examples>',
    "    user: I'm a data scientist investigating what logging we have in place",
    '    assistant: [saves user memory: user is a data scientist, currently focused on observability/logging]',
    '',
    "    user: I've been writing Go for ten years but this is my first time touching the React side of this repo",
    "    assistant: [saves user memory: deep Go expertise, new to React and this project's frontend — frame frontend explanations in terms of backend analogues]",
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>feedback</name>',
    "    <description>Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. These are a very important type of memory to read and write as they allow you to remain coherent and responsive to the way you should approach work in the project. Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious.</description>",
    '    <when_to_save>Any time the user corrects your approach ("no not that", "don\'t", "stop doing X") OR confirms a non-obvious approach worked ("yes exactly", "perfect, keep doing that", accepting an unusual choice without pushback). Corrections are easy to notice; confirmations are quieter — watch for them. In both cases, save what is applicable to future conversations, especially if surprising or not obvious from the code. Include *why* so you can judge edge cases later.</when_to_save>',
    '    <how_to_use>Let these memories guide your behavior so that the user does not need to offer the same guidance twice.</how_to_use>',
    '    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user gave — often a past incident or strong preference) and a **How to apply:** line (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead of blindly following the rule.</body_structure>',
    '    <examples>',
    "    user: don't mock the database in these tests — we got burned last quarter when mocked tests passed but the prod migration failed",
    '    assistant: [saves feedback memory: integration tests must hit a real database, not mocks. Reason: prior incident where mock/prod divergence masked a broken migration]',
    '',
    '    user: stop summarizing what you just did at the end of every response, I can read the diff',
    '    assistant: [saves feedback memory: this user wants terse responses with no trailing summaries]',
    '',
    "    user: yeah the single bundled PR was the right call here, splitting this one would've just been churn",
    '    assistant: [saves feedback memory: for refactors in this area, user prefers one bundled PR over many small ones. Confirmed after I chose this approach — a validated judgment call, not a correction]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>project</name>',
    '    <description>Information that you learn about ongoing work, goals, initiatives, bugs, or incidents within the project that is not otherwise derivable from the code or git history. Project memories help you understand the broader context and motivation behind the work the user is doing within this working directory.</description>',
    '    <when_to_save>When you learn who is doing what, why, or by when. These states change relatively quickly so try to keep your understanding of this up to date. Always convert relative dates in user messages to absolute dates when saving (e.g., "Thursday" → "2026-03-05"), so the memory remains interpretable after time passes.</when_to_save>',
    "    <how_to_use>Use these memories to more fully understand the details and nuance behind the user's request and make better informed suggestions.</how_to_use>",
    '    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation — often a constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this should shape your suggestions). Project memories decay fast, so the why helps future-you judge whether the memory is still load-bearing.</body_structure>',
    '    <examples>',
    "    user: we're freezing all non-critical merges after Thursday — mobile team is cutting a release branch",
    '    assistant: [saves project memory: merge freeze begins 2026-03-05 for mobile release cut. Flag any non-critical PR work scheduled after that date]',
    '',
    "    user: the reason we're ripping out the old auth middleware is that legal flagged it for storing session tokens in a way that doesn't meet the new compliance requirements",
    '    assistant: [saves project memory: auth middleware rewrite is driven by legal/compliance requirements around session token storage, not tech-debt cleanup — scope decisions should favor compliance over ergonomics]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>reference</name>',
    '    <description>Stores pointers to where information can be found in external systems. These memories allow you to remember where to look to find up-to-date information outside of the project directory.</description>',
    '    <when_to_save>When you learn about resources in external systems and their purpose. For example, that bugs are tracked in a specific project in Linear or that feedback can be found in a specific Slack channel.</when_to_save>',
    '    <how_to_use>When the user references an external system or information that may be in an external system.</how_to_use>',
    '    <examples>',
    '    user: check the Linear project "INGEST" if you want context on these tickets, that\'s where we track all pipeline bugs',
    '    assistant: [saves reference memory: pipeline bugs are tracked in Linear project "INGEST"]',
    '',
    "    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches — if you're touching request handling, that's the thing that'll page someone",
    '    assistant: [saves reference memory: grafana.internal/d/api-latency is the oncall latency dashboard — check it when editing request-path code]',
    '    </examples>',
    '</type>',
    '</types>',
)

# 不应保存的内容
WHAT_NOT_TO_SAVE_SECTION: Tuple[str, ...] = (
    '## What NOT to save in memory',
    '',
    '- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.',
    '- Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.',
    '- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.',
    '- Anything already documented in CLAUDE.md files.',
    '- Ephemeral task details: in-progress work, temporary state, current conversation context.',
    '',
    'These exclusions apply even when the user explicitly asks you to save. If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping.',
)

# 记忆漂移警告
MEMORY_DRIFT_CAVEAT = (
    '- Memory records can become stale over time. Use memory as context for what was true at a given point in time. '
    'Before answering the user or building assumptions based solely on information in memory records, verify that the memory is still correct and up-to-date by reading the current state of the files or resources. '
    'If a recalled memory conflicts with current information, trust what you observe now — and update or remove the stale memory rather than acting on it.'
)

# 何时访问记忆
WHEN_TO_ACCESS_SECTION: Tuple[str, ...] = (
    '## When to access memories',
    '- When memories seem relevant, or the user references prior-conversation work.',
    '- You MUST access memory when the user explicitly asks you to check, recall, or remember.',
    '- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md were empty. Do not apply remembered facts, cite, compare against, or mention memory content.',
    MEMORY_DRIFT_CAVEAT,
)

# 如何信任记忆
TRUSTING_RECALL_SECTION: Tuple[str, ...] = (
    '## Before recommending from memory',
    '',
    'A memory that names a specific function, file, or flag is a claim that it existed *when the memory was written*. It may have been renamed, removed, or never merged. Before recommending it:',
    '',
    '- If the memory names a file path: check the file exists.',
    '- If the memory names a function or flag: grep for it.',
    '- If the user is about to act on your recommendation (not just asking about history), verify first.',
    '',
    '"The memory says X exists" is not the same as "X exists now."',
    '',
    'A memory that summarizes repo state (activity logs, architecture snapshots) is frozen in time. If the user asks about *recent* or *current* state, prefer `git log` or reading the code over recalling the snapshot.',
)


# ============================================================================
# 截断工具
# ============================================================================

def truncate_entrypoint_content(raw: str) -> Tuple[str, int, int, bool, bool]:
    """
    截断 MEMORY.md 内容到限制
    
    Args:
        raw: 原始内容
    
    Returns:
        (截断后内容, 行数, 字节数, 是否行截断, 是否字节截断)
    """
    trimmed = raw.strip()
    content_lines = trimmed.split('\n')
    line_count = len(content_lines)
    byte_count = len(trimmed.encode('utf-8'))
    
    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES
    
    if not was_line_truncated and not was_byte_truncated:
        return trimmed, line_count, byte_count, False, False
    
    # 先按行截断
    truncated = trimmed
    if was_line_truncated:
        truncated = '\n'.join(content_lines[:MAX_ENTRYPOINT_LINES])
    
    # 再按字节截断
    if len(truncated.encode('utf-8')) > MAX_ENTRYPOINT_BYTES:
        # 找到最后一个换行符在限制内
        encoded = truncated.encode('utf-8')
        if len(encoded) > MAX_ENTRYPOINT_BYTES:
            # 找到限制内的最后一个换行符
            cut_at = truncated.rfind('\n', 0, MAX_ENTRYPOINT_BYTES)
            if cut_at > 0:
                truncated = truncated[:cut_at]
            else:
                truncated = truncated[:MAX_ENTRYPOINT_BYTES]
    
    return truncated, line_count, byte_count, was_line_truncated, was_byte_truncated
