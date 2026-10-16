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
    'name: {{记忆名称}}',
    'description: {{一行描述 — 用于将来判断相关性，请写具体}}',
    f'type: {{{"|".join(MEMORY_TYPES)}}}',
    '---',
    '',
    '{{记忆内容 — feedback/project 类型请按此结构：先写规则/事实，再写 **Why:**（原因）与 **How to apply:**（应用方式）两行}}',
    '```',
)

# 记忆类型说明（组合模式 — 含共享/私有区分）
TYPES_SECTION_COMBINED: Tuple[str, ...] = (
    '## 记忆类型',
    '',
    '记忆系统支持以下几种类型。每种类型标注了建议的可见范围（私有/共享）以及选择依据。',
    '',
    '<types>',
    '<type>',
    '    <name>user</name>',
    '    <scope>始终私有</scope>',
    '    <description>记录用户的角色、目标、职责与知识背景。好的用户记忆能帮助你按用户偏好与视角调整行为。目标是理解用户是谁、如何对他更有帮助。注意：不要记录对用户的负面评价，也不要记录与当前工作无关的信息。</description>',
    '    <when_to_save>当了解到用户的角色、偏好、职责或知识背景的任何细节时</when_to_save>',
    '    <how_to_use>当工作需要参考用户画像时。例如解释代码时，按用户已有的知识结构来组织说明。</how_to_use>',
    '    <examples>',
    '    用户：我是做数据分析的，最近在看日志体系',
    '    助手：[保存私有用户记忆：用户是数据科学家，当前关注可观测性/日志]',
    '',
    '    用户：我写了十年 Go，这是第一次碰这个仓库的前端',
    '    助手：[保存私有用户记忆：Go 经验丰富，React 与前端是新手 —— 用后端类比来解释前端概念]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>feedback</name>',
    '    <scope>默认私有；当明确是全项目都应遵循的约定（如测试规范、构建约束）时存为共享</scope>',
    '    <description>用户给出的工作方式指导 —— 既包括应避免的，也包括应保持的。这类记忆很重要：只记录纠错会让你回避过去的错误，但逐渐偏离用户已验证过的做法，变得过度保守。纠错与确认都要记录。</description>',
    '    <when_to_save>用户纠正你的做法时（"不对"、"别这样"、"不要 X"），或确认某个不明显的做法有效时（"对，就这样"、"很好，继续保持"）。纠错容易注意到，确认往往更安静 —— 要留心。两种都记录对将来有用的部分，特别是从代码看不出来的。写明原因，便于以后判断边界情况。</when_to_save>',
    '    <how_to_use>用这些记忆调整行为，让用户不需要重复同样的指导。</how_to_use>',
    '    <body_structure>先写规则本身，再写 **Why:**（用户给的原因 —— 通常是过去的事故或强烈偏好）与 **How to apply:**（何时何地适用）。知道原因才能判断边界情况，而不是机械照搬。</body_structure>',
    '    <examples>',
    '    用户：这些测试不要 mock 数据库 —— 上季度 mock 测试全过但生产迁移炸了',
    '    助手：[保存共享反馈记忆：集成测试必须连真实数据库，不用 mock。原因：曾因 mock 与生产差异掩盖了迁移问题。范围：这是项目测试规范]',
    '',
    '    用户：每次回复末尾不用总结刚做了什么，我看 diff 就行',
    '    助手：[保存私有反馈记忆：该用户希望回复简洁，不要结尾总结。私有：这是沟通偏好，不是项目约定]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>project</name>',
    '    <scope>私有或共享，倾向共享</scope>',
    '    <description>关于项目内正在进行的工作、目标、计划、缺陷或事件的信息 —— 代码和 git 历史里看不出来的部分。帮助理解用户当前工作的背景与动机。</description>',
    '    <when_to_save>当了解到谁在做什么、为什么、截止时间等信息时。这类信息变化较快，尽量保持更新。保存时把相对日期换算为绝对日期（如"周四" → "2026-03-05"），保证之后仍可解读。</when_to_save>',
    '    <how_to_use>用这些记忆理解用户请求背后的细节，提前考虑协作问题，给出更贴合的建议。</how_to_use>',
    '    <body_structure>先写事实或决定，再写 **Why:**（动机 —— 通常是约束、截止时间或相关方要求）与 **How to apply:**（如何影响你的建议）。项目记忆衰减快，原因能帮未来的你判断它是否还成立。</body_structure>',
    '    <examples>',
    '    用户：周四之后所有非关键合并都冻结 —— 移动端要切发布分支',
    '    助手：[保存共享项目记忆：2026-03-05 起合并冻结，服务于移动端发布。此日期后的非关键 PR 要提醒]',
    '',
    '    用户：换掉旧认证中间件的真正原因是法务说它的会话令牌存储不合规',
    '    助手：[保存共享项目记忆：认证中间件重写由合规要求驱动，不是技术债清理 —— 方案取舍优先合规]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>reference</name>',
    '    <scope>通常共享</scope>',
    '    <description>记录外部系统中信息的存放位置。帮助记住项目目录之外的信息该去哪里找。</description>',
    '    <when_to_save>当了解到外部系统资源及其用途时。例如缺陷在某平台的某个项目里跟踪，或反馈在某个频道里收集。</when_to_save>',
    '    <how_to_use>当用户提到外部系统或可能存在于外部系统的信息时。</how_to_use>',
    '    <examples>',
    '    用户：流水线的问题都记在工单平台的 INGEST 项目里，要背景去看那里',
    '    助手：[保存共享引用记忆：流水线缺陷跟踪在 INGEST 项目]',
    '',
    '    用户：改请求处理相关代码时记得看延迟看板，值班盯的就是它',
    '    助手：[保存共享引用记忆：请求链路改动前查看延迟看板]',
    '    </examples>',
    '</type>',
    '</types>',
)

# 记忆类型说明（独立模式 — 无共享/私有区分）
TYPES_SECTION_INDIVIDUAL: Tuple[str, ...] = (
    '## 记忆类型',
    '',
    '记忆系统支持以下几种类型：',
    '',
    '<types>',
    '<type>',
    '    <name>user</name>',
    '    <description>记录用户的角色、目标、职责与知识背景。好的用户记忆能帮助你按用户偏好与视角调整行为。目标是理解用户是谁、如何对他更有帮助。注意：不要记录对用户的负面评价，也不要记录与当前工作无关的信息。</description>',
    '    <when_to_save>当了解到用户的角色、偏好、职责或知识背景的任何细节时</when_to_save>',
    '    <how_to_use>当工作需要参考用户画像时。例如解释代码时，按用户已有的知识结构来组织说明。</how_to_use>',
    '    <examples>',
    '    用户：我是做数据分析的，最近在看日志体系',
    '    助手：[保存用户记忆：用户是数据科学家，当前关注可观测性/日志]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>feedback</name>',
    '    <description>用户给出的工作方式指导 —— 既包括应避免的，也包括应保持的。这类记忆很重要：只记录纠错会让你回避过去的错误，但逐渐偏离用户已验证过的做法，变得过度保守。纠错与确认都要记录。</description>',
    '    <when_to_save>用户纠正你的做法时（"不对"、"别这样"、"不要 X"），或确认某个不明显的做法有效时（"对，就这样"、"很好，继续保持"）。纠错容易注意到，确认往往更安静 —— 要留心。两种都记录对将来有用的部分，特别是从代码看不出来的。写明原因，便于以后判断边界情况。</when_to_save>',
    '    <how_to_use>用这些记忆调整行为，让用户不需要重复同样的指导。</how_to_use>',
    '    <body_structure>先写规则本身，再写 **Why:**（用户给的原因）与 **How to apply:**（何时何地适用）。知道原因才能判断边界情况，而不是机械照搬。</body_structure>',
    '    <examples>',
    '    用户：这些测试不要 mock 数据库 —— 上季度 mock 测试全过但生产迁移炸了',
    '    助手：[保存反馈记忆：集成测试必须连真实数据库，不用 mock。原因：曾因 mock 与生产差异掩盖了迁移问题]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>project</name>',
    '    <description>关于项目内正在进行的工作、目标、计划、缺陷或事件的信息 —— 代码和 git 历史里看不出来的部分。帮助理解用户当前工作的背景与动机。</description>',
    '    <when_to_save>当了解到谁在做什么、为什么、截止时间等信息时。这类信息变化较快，尽量保持更新。保存时把相对日期换算为绝对日期（如"周四" → "2026-03-05"）。</when_to_save>',
    '    <how_to_use>用这些记忆理解用户请求背后的细节，给出更贴合的建议。</how_to_use>',
    '    <body_structure>先写事实或决定，再写 **Why:**（动机）与 **How to apply:**（如何影响你的建议）。项目记忆衰减快，原因能帮未来的你判断它是否还成立。</body_structure>',
    '    <examples>',
    '    用户：周四之后所有非关键合并都冻结 —— 移动端要切发布分支',
    '    助手：[保存项目记忆：2026-03-05 起合并冻结，服务于移动端发布]',
    '    </examples>',
    '</type>',
    '<type>',
    '    <name>reference</name>',
    '    <description>记录外部系统中信息的存放位置。帮助记住项目目录之外的信息该去哪里找。</description>',
    '    <when_to_save>当了解到外部系统资源及其用途时。例如缺陷在某平台的某个项目里跟踪，或反馈在某个频道里收集。</when_to_save>',
    '    <how_to_use>当用户提到外部系统或可能存在于外部系统的信息时。</how_to_use>',
    '    <examples>',
    '    用户：流水线的问题都记在工单平台的 INGEST 项目里，要背景去看那里',
    '    助手：[保存引用记忆：流水线缺陷跟踪在 INGEST 项目]',
    '    </examples>',
    '</type>',
    '</types>',
)

# 不应保存的内容
WHAT_NOT_TO_SAVE_SECTION: Tuple[str, ...] = (
    '## 不应保存的内容',
    '',
    '- 代码模式、约定、架构、文件路径或项目结构 —— 这些读一下项目就能知道。',
    '- Git 历史、近期改动、谁改了什么 —— `git log` / `git blame` 才是权威。',
    '- 调试方案或修复步骤 —— 修复在代码里，背景在提交信息里。',
    '- 项目文档中已记录的内容。',
    '- 临时性任务细节：进行中的工作、临时状态、当前对话上下文。',
    '',
    '即使用户明确要求保存，以上排除项同样适用。如果用户要求保存 PR 列表或活动总结，先问清楚其中哪些是*出乎意料*或*不显而易见*的 —— 那才是值得保留的部分。',
)

# 记忆漂移警告
MEMORY_DRIFT_CAVEAT = (
    '- 记忆会随时间失效。把记忆当作"当时为真"的上下文，而不是现状。在仅凭记忆作答或建立假设之前，先读取当前文件或资源核实。'
    '如果记忆与现状冲突，以你观察到的现状为准 —— 并更新或删除失效记忆，而不是照旧使用。'
)

# 何时访问记忆
WHEN_TO_ACCESS_SECTION: Tuple[str, ...] = (
    '## 何时访问记忆',
    '- 当记忆看起来相关，或用户提到之前对话的工作时。',
    '- 用户明确要求查看、回忆或记住时，必须访问记忆。',
    '- 如果用户说*忽略*或*不要使用*记忆：当作记忆为空处理。不要引用、对照或提及记忆内容。',
    MEMORY_DRIFT_CAVEAT,
)

# 如何信任记忆
TRUSTING_RECALL_SECTION: Tuple[str, ...] = (
    '## 引用记忆前先核实',
    '',
    '记忆里提到某个函数、文件或参数，只代表*写入记忆时*它存在过。它可能已被改名、删除，或从未合并。引用前：',
    '',
    '- 记忆提到文件路径：先确认文件存在。',
    '- 记忆提到函数或参数：先搜索确认。',
    '- 用户准备按你的建议行动时（不只是问历史），先核实。',
    '',
    '"记忆说 X 存在"不等于"X 现在存在"。',
    '',
    '总结仓库状态的记忆（活动日志、架构快照）是冻结在过去的。用户问*近期*或*当前*状态时，优先 `git log` 或直接读代码，而不是回忆快照。',
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
