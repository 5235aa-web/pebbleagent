"""
Context Compressor - 完整的上下文压缩器
1. 结构化摘要提示词 - 9部分结构化输出
2. 图片/文档剥离 - stripImagesFromMessages
3. Tool pair 完整性保护 - adjustIndexToPreserveAPIInvariants
4. 压缩边界标记 - SystemCompactBoundaryMessage
5. Plan 文件保留 - createPlanAttachmentIfNeeded

使用 tiktoken 进行精确的 Token 计数
"""

import logging
import re
import uuid
import json
import os
import time
from typing import List, Dict, Any, Optional, Tuple, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import copy

# 导入 Transcript 管理器
from .transcript_manager import (
    get_transcript_manager,
    get_transcript_path,
    TranscriptManager,
)

logger = logging.getLogger(__name__)

# ============================================================================
# 常量配置
# ============================================================================

# 默认配置
DEFAULT_MAX_TOKENS = 90000
DEFAULT_RECENT_MESSAGES = 10

# 压缩后恢复配置
POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5000
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25000

# 错误消息
ERROR_MESSAGE_NOT_ENOUGH_MESSAGES = 'Not enough messages to compact.'
ERROR_MESSAGE_PROMPT_TOO_LONG = 'Conversation too long. Compaction failed after retries.'
ERROR_MESSAGE_INCOMPLETE_RESPONSE = 'Compaction interrupted - please try again.'

# tiktoken encoder 缓存
_encoder = None


# ============================================================================
# 数据类定义
# ============================================================================

class CompactTrigger(Enum):
    """压缩触发类型（2026-10-16：自动/响应式触发已移除，仅保留手动触发）"""
    MANUAL = "manual"


class CompactDirection(Enum):
    """部分压缩方向"""
    FROM = "from"  # 从指定位置向后压缩
    UP_TO = "up_to"  # 压缩到指定位置


@dataclass
class ToolUseBlock:
    """工具调用块"""
    id: str
    name: str
    input: Dict[str, Any]
    

@dataclass
class ToolResultBlock:
    """工具结果块"""
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass
class ContentBlock:
    """消息内容块"""
    type: str  # 'text', 'image', 'document', 'tool_use', 'tool_result'
    text: Optional[str] = None
    source: Optional[Dict[str, Any]] = None  # for image/document
    id: Optional[str] = None  # for tool_use
    name: Optional[str] = None  # for tool_use
    input: Optional[Dict[str, Any]] = None  # for tool_use
    tool_use_id: Optional[str] = None  # for tool_result
    content: Optional[str] = None  # for tool_result
    is_error: bool = False  # for tool_result


@dataclass
class Message:
    """消息对象"""
    uuid: str
    role: str  # 'system', 'user', 'assistant', 'tool'
    content: Any  # str or List[ContentBlock]
    timestamp: float = field(default_factory=time.time)
    message_id: Optional[str] = None  # API 返回的消息 ID（用于 thinking block 合并）
    is_compact_summary: bool = False
    is_meta: bool = False
    reasoning_content: Optional[str] = None  # DeepSeek 思考模式推理内容
    tool_call_id: Optional[str] = None  # tool 消息必需的字段
    tool_calls: Optional[List[Dict[str, Any]]] = None  # assistant 消息的工具调用
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "uuid": self.uuid,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.message_id:
            result["message_id"] = self.message_id
        if self.is_compact_summary:
            result["is_compact_summary"] = self.is_compact_summary
        if self.is_meta:
            result["is_meta"] = self.is_meta
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            result["tool_calls"] = self.tool_calls
        return result


@dataclass
class CompactBoundaryMessage(Message):
    """压缩边界标记消息"""
    compact_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        self.role = "system"
        self.content = f"[COMPACT_BOUNDARY:{self.uuid}]"
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["compact_metadata"] = self.compact_metadata
        result["subtype"] = "compact_boundary"
        return result


@dataclass
class CompactionResult:
    """压缩结果"""
    success: bool
    boundary_marker: Optional[CompactBoundaryMessage] = None
    summary_messages: List[Message] = field(default_factory=list)
    messages_to_keep: List[Message] = field(default_factory=list)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    pre_compact_token_count: int = 0
    post_compact_token_count: int = 0
    true_post_compact_token_count: int = 0
    saved_percent: float = 0.0
    summary: Optional[str] = None
    error: Optional[str] = None
    trigger: CompactTrigger = CompactTrigger.MANUAL


@dataclass
class PlanFile:
    """计划文件"""
    path: str
    content: str
    exists: bool = True


@dataclass
class FileState:
    """文件状态"""
    path: str
    content: str
    timestamp: float


@dataclass
class SkillState:
    """Skill 状态"""
    name: str
    path: str
    content: str
    invoked_at: float


# ============================================================================
# Token 计算工具
# ============================================================================

def get_encoder():
    """
    获取 tiktoken encoder（延迟加载，缓存复用）
    
    使用 cl100k_base 编码，适用于 GPT-4, GPT-4o, GPT-3.5-turbo 等模型
    """
    global _encoder
    if _encoder is None:
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
            logger.info("[ContextCompressor] tiktoken encoder loaded: cl100k_base")
        except Exception as e:
            logger.warning(f"[ContextCompressor] Failed to load tiktoken: {e}, using fallback")
            _encoder = "fallback"  # 标记为降级模式
    return _encoder if _encoder != "fallback" else None


def estimate_tokens(text: str) -> int:
    """
    精确计算文本的 Token 数量
    
    使用 tiktoken 进行精确计算，降级时使用简单估算
    """
    if not text:
        return 0
    
    encoder = get_encoder()
    
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception as e:
            logger.warning(f"[ContextCompressor] tiktoken encode failed: {e}")
    
    # 降级：简单估算
    # 中文约 1.5 字符/token，英文约 4 字符/token
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    total_chars = len(text)
    
    if total_chars == 0:
        return 0
    
    chinese_ratio = chinese_chars / total_chars
    estimated = int(total_chars / (1.5 * chinese_ratio + 4 * (1 - chinese_ratio)))
    return max(estimated, 1)


def estimate_message_tokens(message: Message) -> int:
    """估算单条消息的 Token 数量"""
    total = 0
    
    content = message.content
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += estimate_tokens(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    total += estimate_tokens(json.dumps(block.get("input", {})))
                elif block.get("type") == "tool_result":
                    total += estimate_tokens(block.get("content", ""))
            elif isinstance(block, ContentBlock):
                if block.type == "text":
                    total += estimate_tokens(block.text or "")
                elif block.type == "tool_use":
                    total += estimate_tokens(json.dumps(block.input or {}))
                elif block.type == "tool_result":
                    total += estimate_tokens(block.content or "")
    
    # 每条消息有额外的格式开销
    total += 4
    return total


def estimate_messages_tokens(messages: List[Message]) -> int:
    """估算消息列表的总 Token 数"""
    return sum(estimate_message_tokens(msg) for msg in messages)


def rough_token_count_estimation(text: str) -> int:
    """粗略估算 Token 数量（用于快速计算）"""
    if not text:
        return 0
    # 使用 ~4 字符/token 的估算
    return len(text) // 4


# ============================================================================
# 1. 结构化摘要提示词
# ============================================================================

# 禁止工具调用的前缀
NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

# 详细分析指令
DETAILED_ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

# 基础压缩提示词模板
BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION}

Output exactly the Markdown structure shown below and keep the section order unchanged. Do not add sections beyond those listed.

## Goal
- [single-sentence task summary]

## Constraints & Preferences
- [user constraints, preferences, specs, or "(none)"]

## Progress
### Done
- [completed work or "(none)"]

### In Progress
- [current work or "(none)"]

### Blocked
- [blockers or "(none)"]

## Key Decisions
- [decision and why, or "(none)"]

## Next Steps
- [ordered next actions or "(none)"]

## Critical Context
- [important technical facts, errors, open questions, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, commands, error strings, and identifiers when known.
- Do not mention the summary process or that context was compacted.
- Do not include full code snippets — reference the file path and the nature of the change instead.
- Summarize the conversation CHRONOLOGICALLY and cover EVERY topic from the earliest messages to the latest: completed tasks, ongoing work, and all significant user requests must all appear in the summary. Never skip early topics just because later messages are fresher.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
## Goal
Build a dark-mode toggle for the settings page.

## Constraints & Preferences
- Follow the existing CSS variable pattern in `src/styles/variables.css`
- No additional npm dependencies

## Progress
### Done
- Added `--bg-dark` / `--text-dark` CSS variables to variables.css
- Created `useDarkMode()` composable in `src/composables/useDarkMode.ts`
- Wired toggle button in SettingsPage.vue

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- Used `prefers-color-scheme` media query as the default, with manual toggle override stored in localStorage — users who never touch the toggle still get the OS default.

## Next Steps
- Add transition animation on theme switch (0.3s ease on body background-color)

## Critical Context
- The existing color variables use `--bg-primary` / `--text-primary` naming; dark overrides follow the same convention with `-dark` suffix.
- localStorage key: `theme-preference`, values: `"light"` | `"dark"` | `"system"`

## Relevant Files
- src/styles/variables.css: dark CSS variable overrides
- src/composables/useDarkMode.ts: composable logic (media query + localStorage + reactive state)
- src/views/SettingsPage.vue: toggle button placement
</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response. 

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating your summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
"""

# 部分压缩提示词（FROM 方向）
PARTIAL_COMPACT_FROM_PROMPT = f"""Your task is to create a detailed summary of the RECENT portion of the conversation — the messages that follow earlier retained context. The earlier messages are being kept intact and do NOT need to be summarized. Focus your summary on what was discussed, learned, and accomplished in the recent messages only.

{DETAILED_ANALYSIS_INSTRUCTION}

Output exactly the Markdown structure shown below and keep the section order unchanged. Do not add sections beyond those listed.

## Goal
- [single-sentence task summary of the recent portion]

## Constraints & Preferences
- [user constraints, preferences, specs, or "(none)"]

## Progress
### Done
- [completed work or "(none)"]

### In Progress
- [current work or "(none)"]

### Blocked
- [blockers or "(none)"]

## Key Decisions
- [decision and why, or "(none)"]

## Next Steps
- [ordered next actions or "(none)"]

## Critical Context
- [important technical facts, errors, open questions, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, commands, error strings, and identifiers when known.
- Do not mention the summary process or that context was compacted.
- Do not include full code snippets — reference the file path and the nature of the change instead.
- Summarize the conversation CHRONOLOGICALLY and cover EVERY topic from the earliest messages to the latest: completed tasks, ongoing work, and all significant user requests must all appear in the summary. Never skip early topics just because later messages are fresher.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
## Goal
Refactor the API client to support request retry with exponential backoff.

## Constraints & Preferences
- Must not introduce new dependencies
- Retry logic should be opt-in per request
- Default: 3 retries, 1s base delay

## Progress
### Done
- Added `retryCount` and `baseDelay` options to `ApiClientConfig`
- Implemented `withRetry()` wrapper in `src/api/retry.ts`
- Wired wrapper into `ApiClient.request()` as an optional middleware

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- Used a composable middleware pattern instead of subclassing — keeps the base ApiClient clean and retry logic independently testable.

## Next Steps
- Add unit tests for timeout vs 5xx retry differentiation

## Critical Context
- The existing ApiClient already has a `request()` method that returns `Promise<Response>`; the retry wrapper intercepts at that boundary.
- 5xx and network errors are retried; 4xx are not.

## Relevant Files
- src/api/retry.ts: new file, retry middleware implementation
- src/api/client.ts: added middleware chain support
</summary>
</example>

Please provide your summary based on the RECENT messages only (after the retained earlier context), following this structure and ensuring precision and thoroughness in your response.
"""

# 部分压缩提示词（UP_TO 方向）
PARTIAL_COMPACT_UP_TO_PROMPT = f"""Your task is to create a detailed summary of this conversation. This summary will be placed at the start of a continuing session; newer messages that build on this context will follow after your summary (you do not see them here). Summarize thoroughly so that someone reading only your summary and then the newer messages can fully understand what happened and continue the work.

{DETAILED_ANALYSIS_INSTRUCTION}

Output exactly the Markdown structure shown below and keep the section order unchanged. Do not add sections beyond those listed.

## Goal
- [single-sentence task summary]

## Constraints & Preferences
- [user constraints, preferences, specs, or "(none)"]

## Progress
### Done
- [completed work or "(none)"]

### In Progress
- [current work or "(none)"]

### Blocked
- [blockers or "(none)"]

## Key Decisions
- [decision and why, or "(none)"]

## Next Steps
- [ordered next actions or "(none)"]

## Critical Context
- [important technical facts, errors, open questions, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, commands, error strings, and identifiers when known.
- Do not mention the summary process or that context was compacted.
- Do not include full code snippets — reference the file path and the nature of the change instead.
- Summarize the conversation CHRONOLOGICALLY and cover EVERY topic from the earliest messages to the latest: completed tasks, ongoing work, and all significant user requests must all appear in the summary. Never skip early topics just because later messages are fresher.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
## Goal
Add user authentication with JWT tokens.

## Constraints & Preferences
- Must use the existing `fastapi-users` library already in dependencies
- Token expiry: 24h for access token, 7d for refresh token
- Passwords hashed with bcrypt

## Progress
### Done
- Created `/auth/register` and `/auth/login` endpoints in `api_auth.py`
- Implemented JWT token creation and validation in `auth/jwt.py`
- Added `get_current_user` dependency for protected routes

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- Refresh tokens stored in DB so they can be revoked individually; access tokens are stateless JWTs.

## Next Steps
- Add `/auth/refresh` endpoint
- Write tests for token expiry edge cases

## Critical Context
- `SECRET_KEY` is loaded from env at import time in `auth/jwt.py`; changing it invalidates all existing tokens.
- The `User` model already has a `hashed_password` field.

## Relevant Files
- api_auth.py: register/login endpoints
- auth/jwt.py: token creation/validation
- auth/deps.py: `get_current_user` dependency
</summary>
</example>

Please provide your summary following this structure, ensuring precision and thoroughness in your response.
"""

NO_TOOLS_TRAILER = """

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task."""


def get_compact_prompt(custom_instructions: Optional[str] = None) -> str:
    """获取完整的压缩提示词"""
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT
    
    # 默认要求中文输出压缩报告
    prompt += "\n\nIMPORTANT: You MUST output the summary in Chinese (中文). Section content must be in Chinese, but DO NOT translate the section titles — keep them exactly as ## Goal, ## Progress, ## Key Decisions, etc. The titles are machine-readable keys used by the frontend parser."
    
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    
    prompt += NO_TOOLS_TRAILER
    return prompt


def get_partial_compact_prompt(
    custom_instructions: Optional[str] = None,
    direction: CompactDirection = CompactDirection.FROM
) -> str:
    """获取部分压缩提示词"""
    template = (
        PARTIAL_COMPACT_UP_TO_PROMPT 
        if direction == CompactDirection.UP_TO 
        else PARTIAL_COMPACT_FROM_PROMPT
    )
    prompt = NO_TOOLS_PREAMBLE + template
    
    # 默认要求中文输出压缩报告
    prompt += "\n\nIMPORTANT: You MUST output the summary in Chinese (中文). Section content must be in Chinese, but DO NOT translate the section titles — keep them exactly as ## Goal, ## Progress, ## Key Decisions, etc. The titles are machine-readable keys used by the frontend parser."
    
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"
    
    prompt += NO_TOOLS_TRAILER
    return prompt


def format_compact_summary(summary: str) -> str:
    """
    格式化压缩摘要：
    1. 移除 <analysis> 草稿块
    2. 将 <summary> 标签替换为可读的章节标题

    兼容 LLM 输出被 max_tokens 截断的情况（有 <summary> 但无 </summary> 闭合标签，
    或连标签都没有）：从断点处提取剩余内容兜底，避免摘要丢失。
    """
    formatted = summary

    # 移除 analysis 部分
    formatted = re.sub(r'<analysis>[\s\S]*?</analysis>', '', formatted)

    # 提取并格式化 summary 部分
    summary_match = re.search(r'<summary>([\s\S]*?)</summary>', formatted)
    if summary_match:
        content = summary_match.group(1) or ''
        # 用 lambda 避免 content 中的 \w \s 等被 re.sub 当成正则转义
        formatted = re.sub(
            r'<summary>[\s\S]*?</summary>',
            lambda _: f'Summary:\n{content.strip()}',
            formatted
        )
    else:
        # 兼容输出被截断：有 <summary> 开标签但无闭合标签
        open_match = re.search(r'<summary>\s*([\s\S]*)$', formatted)
        if open_match and open_match.group(1).strip():
            formatted = f'Summary:\n{open_match.group(1).strip()}'
        else:
            # 连 <summary> 标签都没有：清理残留标签后整体作为摘要
            cleaned = re.sub(r'</?summary>', '', formatted).strip()
            if cleaned:
                formatted = f'Summary:\n{cleaned}'

    # 清理多余空行
    formatted = re.sub(r'\n\n+', '\n\n', formatted)

    return formatted.strip()


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up_questions: bool = False,
    transcript_path: Optional[str] = None,
    recent_messages_preserved: bool = False
) -> str:
    """
    生成压缩后的用户摘要消息
    """
    formatted_summary = format_compact_summary(summary)
    
    base_summary = f"""This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

{formatted_summary}"""
    
    if transcript_path:
        base_summary += f"\n\nIf you need specific details from before compaction (like exact code snippets, error messages, or content you generated), read the full transcript at: {transcript_path}"
    
    if recent_messages_preserved:
        base_summary += "\n\nRecent messages are preserved verbatim."
    
    if suppress_follow_up_questions:
        continuation = f"""{base_summary}
Continue the conversation from where it left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened."""
        return continuation
    
    return base_summary


# ============================================================================
# 2. 图片/文档剥离
# ============================================================================

def strip_images_from_messages(messages: List[Message]) -> List[Message]:
    """
    从用户消息中剥离图片/文档块
    
    图片对于生成摘要不是必需的，而且可能导致压缩请求本身超长。
    将图片块替换为文本标记，以便摘要仍然记录有图片被分享过。
    
    注意：只有用户消息包含图片（直接附加或在 tool_result 内容中）。
    助手消息包含 text、tool_use 和 thinking 块，但不包含图片。
    """
    result = []
    
    for message in messages:
        if message.role != "user":
            result.append(message)
            continue
        
        content = message.content
        if not isinstance(content, list):
            result.append(message)
            continue
        
        has_media_block = False
        new_content = []
        
        for block in content:
            block_dict = block if isinstance(block, dict) else block.__dict__ if hasattr(block, '__dict__') else {}
            block_type = block_dict.get("type", "")
            
            if block_type == "image":
                has_media_block = True
                new_content.append({"type": "text", "text": "[image]"})
            elif block_type == "document":
                has_media_block = True
                new_content.append({"type": "text", "text": "[document]"})
            elif block_type == "tool_result":
                # 处理嵌套在 tool_result 中的图片/文档
                tool_content = block_dict.get("content")
                if isinstance(tool_content, list):
                    tool_has_media = False
                    new_tool_content = []
                    for item in tool_content:
                        item_type = item.get("type", "") if isinstance(item, dict) else ""
                        if item_type == "image":
                            tool_has_media = True
                            new_tool_content.append({"type": "text", "text": "[image]"})
                        elif item_type == "document":
                            tool_has_media = True
                            new_tool_content.append({"type": "text", "text": "[document]"})
                        else:
                            new_tool_content.append(item)
                    if tool_has_media:
                        has_media_block = True
                        new_block = dict(block_dict)
                        new_block["content"] = new_tool_content
                        new_content.append(new_block)
                    else:
                        new_content.append(block)
                else:
                    new_content.append(block)
            else:
                new_content.append(block)
        
        if not has_media_block:
            result.append(message)
        else:
            # 创建新消息，保留其他属性
            new_message = Message(
                uuid=message.uuid,
                role=message.role,
                content=new_content,
                timestamp=message.timestamp,
                message_id=message.message_id,
                is_compact_summary=message.is_compact_summary,
                is_meta=message.is_meta,
                tool_call_id=message.tool_call_id,
                tool_calls=message.tool_calls
            )
            result.append(new_message)
    
    return result


def strip_reinjected_attachments(messages: List[Message]) -> List[Message]:
    """
    剥离会在压缩后重新注入的附件类型
    
    skill_discovery/skill_listing 会在压缩后通过 resetSentSkillNames() 
    和下一个 turn 的 discovery signal 重新出现，所以将它们喂给摘要器
    会浪费 token 并用过时的 skill 建议污染摘要。
    """
    result = []
    for msg in messages:
        # 检查是否是 attachment 消息
        if msg.role == "attachment":
            attachment_type = None
            if isinstance(msg.content, dict):
                attachment_type = msg.content.get("type")
            if attachment_type in ("skill_discovery", "skill_listing"):
                continue  # 跳过这些附件
        result.append(msg)
    return result


# ============================================================================
# 3. Tool pair 完整性保护
# ============================================================================

def get_tool_result_ids(message: Message) -> List[str]:
    """获取消息中所有 tool_result 块的 tool_use_id"""
    if message.role != "user":
        return []
    
    content = message.content
    if not isinstance(content, list):
        return []
    
    ids = []
    for block in content:
        block_dict = block if isinstance(block, dict) else block.__dict__ if hasattr(block, '__dict__') else {}
        if block_dict.get("type") == "tool_result":
            tool_use_id = block_dict.get("tool_use_id")
            if tool_use_id:
                ids.append(tool_use_id)
    return ids


def has_tool_use_with_ids(message: Message, tool_use_ids: Set[str]) -> bool:
    """检查消息是否包含指定 ID 的 tool_use 块"""
    if message.role != "assistant":
        return False
    
    content = message.content
    if not isinstance(content, list):
        return False
    
    for block in content:
        block_dict = block if isinstance(block, dict) else block.__dict__ if hasattr(block, '__dict__') else {}
        if block_dict.get("type") == "tool_use":
            block_id = block_dict.get("id")
            if block_id and block_id in tool_use_ids:
                return True
    return False


def get_message_ids_in_range(messages: List[Message], start_idx: int) -> Set[str]:
    """获取指定范围内所有助手消息的 message_id"""
    message_ids = set()
    for i in range(start_idx, len(messages)):
        msg = messages[i]
        if msg.role == "assistant" and msg.message_id:
            message_ids.add(msg.message_id)
    return message_ids


def adjust_index_to_preserve_api_invariants(
    messages: List[Message],
    start_index: int
) -> int:
    """
    调整起始索引以确保不分割 tool_use/tool_result 对或共享相同 message.id 的 thinking 块
    
    如果我们保留的任何消息包含 tool_result 块，我们需要包含包含匹配 tool_use 块的前置助手消息。
    
    此外，如果保留范围内的任何助手消息与前置助手消息具有相同的 message.id
    （可能包含 thinking 块），我们需要包含这些消息，以便 normalizeMessagesForAPI 
    可以正确合并它们。
    
    示例场景：
      会话存储（压缩前）：
        Index N:   assistant, message.id: X, content: [thinking]
        Index N+1: assistant, message.id: X, content: [tool_use: ORPHAN_ID]
        Index N+2: assistant, message.id: X, content: [tool_use: VALID_ID]
        Index N+3: user, content: [tool_result: ORPHAN_ID, tool_result: VALID_ID]
      
      如果 start_index = N+2：
        - 旧代码：只检查消息 N+2 的 tool_results，没找到，返回 N+2
        - 切片和合并后：
          msg[1]: assistant with [tool_use: VALID_ID]  (ORPHAN tool_use 被排除！)
          msg[2]: user with [tool_result: ORPHAN_ID, tool_result: VALID_ID]
        - API 错误：孤立的 tool_result 引用不存在的 tool_use
    """
    if start_index <= 0 or start_index >= len(messages):
        return start_index
    
    adjusted_index = start_index
    
    # 步骤 1：处理 tool_use/tool_result 配对
    # 收集保留范围内所有消息的 tool_result ID
    all_tool_result_ids = []
    for i in range(start_index, len(messages)):
        all_tool_result_ids.extend(get_tool_result_ids(messages[i]))
    
    if all_tool_result_ids:
        # 收集保留范围内已有的 tool_use ID
        tool_use_ids_in_kept_range = set()
        for i in range(adjusted_index, len(messages)):
            msg = messages[i]
            if msg.role == "assistant":
                content = msg.content
                if isinstance(content, list):
                    for block in content:
                        block_dict = block if isinstance(block, dict) else block.__dict__ if hasattr(block, '__dict__') else {}
                        if block_dict.get("type") == "tool_use":
                            block_id = block_dict.get("id")
                            if block_id:
                                tool_use_ids_in_kept_range.add(block_id)
        
        # 只查找不在保留范围内的 tool_use
        needed_tool_use_ids = set(
            tid for tid in all_tool_result_ids 
            if tid not in tool_use_ids_in_kept_range
        )
        
        # 向后查找包含匹配 tool_use 块的助手消息
        for i in range(adjusted_index - 1, -1, -1):
            if not needed_tool_use_ids:
                break
            message = messages[i]
            if has_tool_use_with_ids(message, needed_tool_use_ids):
                adjusted_index = i
                # 从集合中移除找到的 tool_use_id
                if message.role == "assistant":
                    content = message.content
                    if isinstance(content, list):
                        for block in content:
                            block_dict = block if isinstance(block, dict) else block.__dict__ if hasattr(block, '__dict__') else {}
                            if block_dict.get("type") == "tool_use":
                                block_id = block_dict.get("id")
                                if block_id and block_id in needed_tool_use_ids:
                                    needed_tool_use_ids.discard(block_id)
    
    # 步骤 2：处理与保留的助手消息共享 message.id 的 thinking 块
    # 收集保留范围内所有助手消息的 message.id
    message_ids_in_kept_range = get_message_ids_in_range(messages, adjusted_index)
    
    # 向后查找具有相同 message.id 的助手消息
    for i in range(adjusted_index - 1, -1, -1):
        message = messages[i]
        if (message.role == "assistant" and 
            message.message_id and 
            message.message_id in message_ids_in_kept_range):
            # 此消息与保留范围内的消息具有相同的 message.id
            # 包含它以便 thinking 块可以正确合并
            adjusted_index = i
    
    return adjusted_index


# ============================================================================
# 4. PTL 重试机制
# ============================================================================

def group_messages_by_api_round(messages: List[Message]) -> List[List[Message]]:
    """
    按 API 轮次边界分组消息：每个 API 往返一组
    
    当新的助手响应开始时（与先前助手不同的 message.id）触发边界。
    对于格式良好的对话，这是 API 安全的分割点——API 契约要求每个 tool_use
    在下一个助手轮次之前被解析，所以配对有效性由助手 id 边界决定。
    """
    groups = []
    current = []
    last_assistant_id = None
    
    for msg in messages:
        if msg.role == "assistant" and msg.message_id:
            if msg.message_id != last_assistant_id and current:
                groups.append(current)
                current = [msg]
            else:
                current.append(msg)
            last_assistant_id = msg.message_id
        else:
            current.append(msg)
    
    if current:
        groups.append(current)
    
    return groups

# ============================================================================
# 5. 压缩边界标记
# ============================================================================

def create_compact_boundary_message(
    trigger: CompactTrigger,
    pre_compact_token_count: int,
    last_message_uuid: Optional[str] = None,
    user_feedback: Optional[str] = None,
    messages_summarized: Optional[int] = None
) -> CompactBoundaryMessage:
    """
    创建压缩边界标记消息
    
    这个标记记录了压缩发生的位置和元数据，用于：
    1. 会话恢复时识别压缩边界
    2. 追踪压缩历史
    3. 支持部分压缩的方向标记
    """
    boundary_uuid = str(uuid.uuid4())
    
    metadata = {
        "trigger": trigger.value,
        "pre_compact_token_count": pre_compact_token_count,
        "timestamp": datetime.now().isoformat(),
        "boundary_uuid": boundary_uuid,
    }
    
    if last_message_uuid:
        metadata["last_message_uuid"] = last_message_uuid
    
    if user_feedback:
        metadata["user_feedback"] = user_feedback
    
    if messages_summarized is not None:
        metadata["messages_summarized"] = messages_summarized
    
    return CompactBoundaryMessage(
        uuid=boundary_uuid,
        role="system",
        content=f"[COMPACT_BOUNDARY:{boundary_uuid}]",
        compact_metadata=metadata
    )


def annotate_boundary_with_preserved_segment(
    boundary: CompactBoundaryMessage,
    anchor_uuid: str,
    messages_to_keep: List[Message]
) -> CompactBoundaryMessage:
    """
    为保留的消息段注释边界标记
    
    保留的消息在磁盘上保持其原始 parentUuid（去重跳过）；
    加载器使用此信息修补 head→anchor 和 anchor's-other-children→tail。
    """
    if not messages_to_keep:
        return boundary
    
    boundary.compact_metadata["preserved_segment"] = {
        "head_uuid": messages_to_keep[0].uuid,
        "anchor_uuid": anchor_uuid,
        "tail_uuid": messages_to_keep[-1].uuid,
    }
    
    return boundary


def is_compact_boundary_message(message: Message) -> bool:
    """检查消息是否是压缩边界标记"""
    if isinstance(message, CompactBoundaryMessage):
        return True
    if message.role == "system":
        if isinstance(message.content, str) and message.content.startswith("[COMPACT_BOUNDARY:"):
            return True
        if hasattr(message, 'compact_metadata'):
            return True
    return False


def get_messages_after_compact_boundary(messages: List[Message]) -> List[Message]:
    """获取最后一个压缩边界之后的消息"""
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            return messages[i + 1:]
    return messages


# ============================================================================
# 6. Plan 文件保留
# ============================================================================

class PlanManager:
    """计划文件管理器"""
    
    _instance = None
    _plans: Dict[str, PlanFile] = {}  # agent_id -> PlanFile
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_plan(cls, agent_id: Optional[str] = None) -> Optional[PlanFile]:
        """获取计划文件"""
        key = agent_id or "main"
        return cls._plans.get(key)
    
    @classmethod
    def set_plan(cls, agent_id: Optional[str], path: str, content: str) -> None:
        """设置计划文件"""
        key = agent_id or "main"
        cls._plans[key] = PlanFile(path=path, content=content, exists=True)
    
    @classmethod
    def clear_plan(cls, agent_id: Optional[str] = None) -> None:
        """清除计划文件"""
        key = agent_id or "main"
        if key in cls._plans:
            del cls._plans[key]


def create_plan_attachment_if_needed(
    agent_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    如果存在计划文件，创建计划附件
    
    这确保压缩后计划内容被保留
    """
    plan = PlanManager.get_plan(agent_id)
    
    if not plan or not plan.exists:
        return None
    
    return {
        "type": "plan_file_reference",
        "plan_file_path": plan.path,
        "plan_content": plan.content,
    }


def create_skill_attachment_if_needed(
    invoked_skills: Dict[str, SkillState],
    agent_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    创建已调用 skill 的附件以在压缩后保留其内容
    
    只包含作用域为给定 agent 的 skill（或 agent_id 为 null/undefined 时的主会话）。
    """
    # 过滤出属于当前 agent 的 skill
    relevant_skills = [
        skill for skill in invoked_skills.values()
        # 可以根据 agent_id 过滤
    ]
    
    if not relevant_skills:
        return None
    
    # 按最近调用时间排序
    relevant_skills.sort(key=lambda s: s.invoked_at, reverse=True)
    
    # 截断每个 skill 的内容以符合 token 预算
    used_tokens = 0
    skills_to_include = []
    
    for skill in relevant_skills:
        truncated_content = truncate_to_tokens(
            skill.content, 
            POST_COMPACT_MAX_TOKENS_PER_SKILL
        )
        tokens = rough_token_count_estimation(truncated_content)
        
        if used_tokens + tokens > POST_COMPACT_SKILLS_TOKEN_BUDGET:
            continue
        
        used_tokens += tokens
        skills_to_include.append({
            "name": skill.name,
            "path": skill.path,
            "content": truncated_content,
        })
    
    if not skills_to_include:
        return None
    
    return {
        "type": "invoked_skills",
        "skills": skills_to_include,
    }


def truncate_to_tokens(content: str, max_tokens: int) -> str:
    """截断内容到指定 token 数量，保留头部"""
    if rough_token_count_estimation(content) <= max_tokens:
        return content
    
    truncation_marker = "\n\n[... content truncated for compaction; read the full file if needed]"
    char_budget = max_tokens * 4 - len(truncation_marker)
    return content[:char_budget] + truncation_marker


# ============================================================================
# 8. PTL 错误检测
# ============================================================================


def is_prompt_too_long_error(error: Any) -> Tuple[bool, Optional[int], Optional[int]]:
    """
    检查是否是 prompt_too_long 错误
    
    Returns:
        (是否是 PTL 错误, token gap, 模型的上下文限制 token 数)
        token_gap 和 context_limit 在无法解析时为 None
    """
    error_str = str(error).lower()
    
    if "prompt_too_long" not in error_str and "context length" not in error_str:
        return False, None, None
    
    # 尝试多种格式提取 (limit, actual) 或 (gap)
    limit = None
    actual = None
    gap = None
    
    # 格式1: "prompt is too long: 150000 tokens > 128000"
    match = re.search(r'(\d+)\s*tokens?\s*>\s*(\d+)', error_str)
    if match:
        actual = int(match.group(1))
        limit = int(match.group(2))
    
    # 格式2: OpenAI "maximum context length is X tokens. However, you requested Y tokens"
    if limit is None:
        match = re.search(r'maximum context length is (\d+) tokens.*?you requested (\d+) tokens', error_str)
        if match:
            limit = int(match.group(1))
            actual = int(match.group(2))
    
    # 格式3: Anthropic "prompt is too long: X tokens > Y tokens"
    if limit is None:
        match = re.search(r'prompt is too long:?\s*(\d+)\s*tokens?\s*>\s*(\d+)', error_str)
        if match:
            actual = int(match.group(1))
            limit = int(match.group(2))
    
    # 格式4: "reducing by X tokens"
    match = re.search(r'reducing by (\d+)', error_str)
    if match:
        gap = int(match.group(1))
    
    # 格式5: 尝试独立提取 limit 和 actual (any format with "X tokens")
    if limit is None:
        # 尝试找 "limit is X", "maximum is X" 等模式
        match = re.search(r'(?:limit|maximum|context)[^\d]*?(\d+)\s*(?:token)', error_str)
        if match:
            limit = int(match.group(1))
    
    if actual is not None and limit is not None:
        return True, actual - limit, limit
    elif gap is not None:
        return True, gap, limit
    elif limit is not None:
        return True, None, limit
    else:
        return True, None, None


# ============================================================================
# 核心压缩函数
# ============================================================================

def compact_conversation(
    messages: List[Message],
    llm_client: Any = None,
    llm_model: str = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    recent_messages: int = DEFAULT_RECENT_MESSAGES,
    custom_instructions: Optional[str] = None,
    trigger: CompactTrigger = CompactTrigger.MANUAL,
    direction: CompactDirection = CompactDirection.FROM,
    pivot_index: Optional[int] = None,
    user_feedback: Optional[str] = None,
    plan_content: Optional[str] = None,
    invoked_skills: Optional[Dict[str, SkillState]] = None,
    file_states: Optional[Dict[str, FileState]] = None,
    session_id: Optional[str] = None,
    project_path: Optional[str] = None,
) -> CompactionResult:
    """
    压缩对话的核心函数
    
    实现完整的压缩流程：
    1. 检查是否需要压缩
    2. 剥离图片/文档
    3. 保护 tool pair 完整性
    4. PTL 重试机制
    5. 生成结构化摘要
    6. 创建压缩边界标记
    7. 保留 Plan 文件和 Skill
    8. 写入被压缩的消息到 Transcript
    9. 返回压缩结果
    
    Args:
        messages: 消息列表
        llm_client: LLM 客户端
        llm_model: 模型名称
        max_tokens: 最大 token 数
        recent_messages: 保留的最近消息数
        custom_instructions: 自定义指令
        trigger: 压缩触发类型
        direction: 部分压缩方向
        pivot_index: 部分压缩的枢轴索引
        user_feedback: 用户反馈
        plan_content: 计划内容
        invoked_skills: 已调用的 skill
        file_states: 文件状态
        session_id: 会话 ID（用于写入 transcript）
        project_path: 项目路径（用于写入 transcript）
    
    Returns:
        CompactionResult 压缩结果
    """
    # 1. 检查消息数量
    if not messages:
        return CompactionResult(
            success=False,
            error=ERROR_MESSAGE_NOT_ENOUGH_MESSAGES,
            trigger=trigger
        )
    
    # 2. 计算压缩前 token 数
    pre_compact_token_count = estimate_messages_tokens(messages)
    
    # 3. 日志记录压缩参数
    logger.info(f"[ContextCompressor] Manual compress requested: {pre_compact_token_count} tokens, target: {max_tokens} tokens")
    
    # 4. 分离系统消息和对话消息
    system_messages = [m for m in messages if m.role == "system"]
    dialog_messages = [m for m in messages if m.role != "system"]
    
    # 5. 检查对话消息数量（至少 2 条才能压缩：压缩 >=1 条、保留 >=1 条）
    if len(dialog_messages) < 2:
        return CompactionResult(
            success=False,
            error=f"对话消息数量不足 ({len(dialog_messages)} < 2)",
            pre_compact_token_count=pre_compact_token_count,
            trigger=trigger
        )
    
    # 6. 部分压缩处理
    if pivot_index is not None:
        if direction == CompactDirection.UP_TO:
            messages_to_summarize = dialog_messages[:pivot_index]
            messages_to_keep = [
                m for m in dialog_messages[pivot_index:]
                if not is_compact_boundary_message(m) and not m.is_compact_summary
            ]
        else:  # FROM
            messages_to_summarize = dialog_messages[pivot_index:]
            messages_to_keep = dialog_messages[:pivot_index]
        
        if not messages_to_summarize:
            return CompactionResult(
                success=False,
                error="没有消息需要压缩",
                pre_compact_token_count=pre_compact_token_count,
                trigger=trigger
            )
    else:
        # 标准压缩：保留最近消息（动态保留数：最多保留 recent_messages 条；
        # 消息不足时至少保留 1 条、保证至少压缩 1 条）
        effective_keep = min(recent_messages, len(dialog_messages) - 1)
        messages_to_summarize = dialog_messages[:-effective_keep]
        messages_to_keep = dialog_messages[-effective_keep:]
    
    # 7. Tool pair 完整性保护
    if pivot_index is None:
        # 计算保留消息的起始索引
        keep_start_index = len(system_messages) + len(messages_to_summarize)
        adjusted_index = adjust_index_to_preserve_api_invariants(messages, keep_start_index)
        
        if adjusted_index != keep_start_index:
            # 调整分割点
            messages_to_summarize = dialog_messages[:adjusted_index - len(system_messages)]
            messages_to_keep = dialog_messages[adjusted_index - len(system_messages):]
            logger.info(f"[ContextCompressor] Adjusted index for tool pair integrity: {keep_start_index} -> {adjusted_index}")
        
        # 防御：tool pair 保护可能把保留范围扩展到全部消息（待压缩集变空）
        if not messages_to_summarize:
            return CompactionResult(
                success=False,
                error="没有消息需要压缩（tool 配对完整性保护不允许拆分）",
                pre_compact_token_count=pre_compact_token_count,
                trigger=trigger
            )
    
    # 8. 剥离图片/文档
    messages_to_summarize_stripped = strip_images_from_messages(messages_to_summarize)
    messages_to_summarize_stripped = strip_reinjected_attachments(messages_to_summarize_stripped)
    
    # 9. 生成摘要
    summary = generate_structured_summary(
        messages=messages_to_summarize_stripped,
        llm_client=llm_client,
        llm_model=llm_model,
        custom_instructions=custom_instructions,
        direction=direction if pivot_index else None,
        input_budget=max_tokens if max_tokens else DEFAULT_SUMMARY_INPUT_BUDGET
    )
    
    if not summary:
        return CompactionResult(
            success=False,
            error="生成摘要失败",
            pre_compact_token_count=pre_compact_token_count,
            trigger=trigger
        )
    
    # 10. 创建压缩边界标记
    boundary_marker = create_compact_boundary_message(
        trigger=trigger,
        pre_compact_token_count=pre_compact_token_count,
        last_message_uuid=messages[-1].uuid if messages else None,
        user_feedback=user_feedback,
        messages_summarized=len(messages_to_summarize)
    )
    
    # 11. 创建摘要消息
    summary_message = Message(
        uuid=str(uuid.uuid4()),
        role="user",
        content=get_compact_user_summary_message(
            summary=summary,
            suppress_follow_up_questions=False,
            recent_messages_preserved=bool(messages_to_keep)
        ),
        is_compact_summary=True
    )
    
    # 12. 创建附件
    attachments = []
    
    # Plan 附件
    if plan_content:
        plan_attachment = create_plan_attachment_if_needed()
        if plan_attachment:
            attachments.append(plan_attachment)
    
    # Skill 附件
    if invoked_skills:
        skill_attachment = create_skill_attachment_if_needed(invoked_skills)
        if skill_attachment:
            attachments.append(skill_attachment)
    
    # 文件状态附件
    if file_states:
        file_attachments = create_post_compact_file_attachments(
            file_states=file_states,
            max_files=POST_COMPACT_MAX_FILES_TO_RESTORE
        )
        attachments.extend(file_attachments)
    
    # 13. 构建压缩后的消息列表
    compressed_messages = system_messages.copy()
    compressed_messages.append(boundary_marker)
    compressed_messages.append(summary_message)
    compressed_messages.extend(messages_to_keep)
    
    # 14. 计算压缩后 token 数
    post_compact_token_count = estimate_messages_tokens(compressed_messages)
    saved_percent = round((pre_compact_token_count - post_compact_token_count) / pre_compact_token_count * 100, 1)
    
    logger.info(f"[ContextCompressor] Compressed: {pre_compact_token_count} -> {post_compact_token_count} tokens ({saved_percent}% saved)")
    
    # 15. 写入被压缩的消息到 Transcript（压缩时保存历史）
    # 压缩前保存被压缩的消息，以便后续查阅
    transcript_manager = get_transcript_manager()
    
    # 将被压缩的消息转换为字典格式
    messages_to_summarize_dicts = [msg.to_dict() if hasattr(msg, 'to_dict') else {
        "uuid": msg.uuid,
        "role": msg.role,
        "content": msg.content,
        "timestamp": getattr(msg, 'timestamp', None),
    } for msg in messages_to_summarize]
    
    # 写入 transcript
    if messages_to_summarize_dicts:
        try:
            transcript_manager.write_session_transcript_segment(
                messages=messages_to_summarize_dicts,
                session_id=session_id,
                project_path=project_path
            )
            logger.info(f"[ContextCompressor] Wrote {len(messages_to_summarize_dicts)} messages to transcript")
        except Exception as e:
            logger.warning(f"[ContextCompressor] Failed to write transcript segment: {e}")
    
    # 16. 重新追加会话元数据（如果有）
    try:
        transcript_manager.re_append_session_metadata()
    except Exception as e:
        logger.debug(f"[ContextCompressor] Failed to re-append session metadata: {e}")
    
    return CompactionResult(
        success=True,
        boundary_marker=boundary_marker,
        summary_messages=[summary_message],
        messages_to_keep=messages_to_keep,
        attachments=attachments,
        pre_compact_token_count=pre_compact_token_count,
        post_compact_token_count=post_compact_token_count,
        true_post_compact_token_count=post_compact_token_count,
        saved_percent=saved_percent,
        summary=summary,
        trigger=trigger
    )


# 必需的摘要段落标题（用于格式校验）
_REQUIRED_SECTIONS = [
    '## Goal',
    '## Constraints',
    '## Progress',
    '## Key Decisions',
    '## Next Steps',
    '## Critical Context',
    '## Relevant Files',
]


def _validate_summary_format(summary: str) -> tuple:
    """
    校验 LLM 输出的摘要是否包含必需的段落标题。
    返回 (是否通过, 缺失的标题列表)。
    至少需要 4/7 个标题匹配才视为通过。
    """
    # 先剥离 <analysis> 和 <summary> 标签，拿到纯内容
    content = summary
    m = re.search(r'<summary>([\s\S]*?)</summary>', summary)
    if m:
        content = m.group(1)
    content = re.sub(r'<analysis>[\s\S]*?</analysis>', '', content)
    
    found = []
    missing = []
    for section in _REQUIRED_SECTIONS:
        if section in content:
            found.append(section)
        else:
            missing.append(section)
    
    # 至少需要 4 个（因为 Constraints 和 Critical Context 有时会被合并/省略）
    return len(found) >= 4, missing


def _retry_summary_with_strict_prompt(
    llm_client,
    llm_model: str,
    conversation_content: str,
    direction=None,
    custom_instructions=None
) -> Optional[str]:
    """用更严格的提示词重试摘要生成"""
    strict_prefix = """⚠️ CRITICAL FORMAT REQUIREMENT — FAILURE MEANS YOUR OUTPUT IS USELESS:

You MUST output EXACTLY this structure with EXACTLY these English section titles. The section titles are machine-readable keys parsed by frontend code. If you translate them or change them, the parser will produce an EMPTY result and your summary will be DISCARDED.

Output format — COPY THESE EXACT TITLES:
<analysis>
[your analysis in Chinese]
</analysis>

<summary>
## Goal
- [one sentence in Chinese]

## Constraints & Preferences
- [items or (none)]

## Progress
### Done
- [items or (none)]
### In Progress
- [items or (none)]
### Blocked
- [items or (none)]

## Key Decisions
- [items or (none)]

## Next Steps
- [items or (none)]

## Critical Context
- [items or (none)]

## Relevant Files
- [items or (none)]
</summary>

DO NOT: translate titles, merge sections, skip sections, or invent new section names.
EVERY section above MUST appear exactly as shown, even if its content is just "(none)".

"""
    
    try:
        response = llm_client.chat.completions.create(
            model=llm_model or "gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": f"{strict_prefix}\n\n对话历史：\n{conversation_content}"
                }
            ],
            max_tokens=8000,  # 与首轮一致，保证重试时输出空间充足
            temperature=0.1  # 更低温度，更确定性
        )
        summary = response.choices[0].message.content
        logger.info(f"[ContextCompressor] Retry summary: {len(summary)} chars")
        return summary
    except Exception as e:
        logger.warning(f"[ContextCompressor] Retry failed: {e}")
        return None


def _force_wrap_summary(raw: str) -> str:
    """强制包裹：把 LLM 的自由格式输出塞进可解析的结构中"""
    # 剥离可能存在的标签
    content = re.sub(r'<analysis>[\s\S]*?</analysis>', '', raw)
    m = re.search(r'<summary>([\s\S]*?)</summary>', content)
    if m:
        content = m.group(1)
    content = content.strip()
    
    # 如果内容已被验证至少有一些标题，直接返回（不应该走到这里）
    # 否则用强制格式包裹
    return f"""<summary>
## Goal
{content[:500]}

## Constraints & Preferences
(none)

## Progress
### Done
(none)
### In Progress
(none)
### Blocked
(none)

## Key Decisions
(none)

## Next Steps
(none)

## Critical Context
(none)

## Relevant Files
(none)
</summary>"""


# 摘要输入预算（tokens）——防止长对话全量拼接导致 LLM 只关注尾部 / 请求超限
DEFAULT_SUMMARY_INPUT_BUDGET = 40000


def _summarize_message_content(msg: Message, char_budget: int) -> str:
    """按字符预算提取单条消息的文本内容（长消息保留开头 + 结尾）"""
    if isinstance(msg.content, str):
        text = msg.content
    elif isinstance(msg.content, list):
        parts = []
        for block in msg.content:
            block_dict = block if isinstance(block, dict) else getattr(block, '__dict__', {})
            if block_dict.get("type") == "text":
                parts.append(block_dict.get("text", "") or "")
            elif block_dict.get("type") == "tool_use":
                parts.append(
                    "[工具调用: %s]\n%s" % (
                        block_dict.get("name", "unknown"),
                        json.dumps(block_dict.get("input", {}), ensure_ascii=False)[:300]
                    )
                )
            elif block_dict.get("type") == "tool_result":
                parts.append("[工具结果]\n%s" % ((block_dict.get("content", "") or "")[:300]))
        text = "\n".join(parts)
    else:
        text = str(msg.content)
    if len(text) <= char_budget:
        return text
    marker = "\n[... 已省略 %d 字符 ...]\n" % (len(text) - char_budget)
    usable = char_budget - len(marker)
    if usable < 50:
        usable = max(char_budget // 2, 10)
    head_len = int(usable * 0.6)
    tail_len = usable - head_len
    return text[:head_len] + marker + text[-tail_len:]


def generate_structured_summary(
    messages: List[Message],
    llm_client: Any = None,
    llm_model: str = None,
    custom_instructions: Optional[str] = None,
    direction: Optional[CompactDirection] = None,
    input_budget: Optional[int] = None
) -> Optional[str]:
    """
    生成结构化摘要

    使用 9 部分结构化提示词生成高质量摘要。

    输入预算控制（input_budget，默认 DEFAULT_SUMMARY_INPUT_BUDGET）：
    - 最新优先裁剪：从最新消息向前累计 token，超出预算的最早消息丢弃并注明
    - 自适应截断：位置越新额度越高，用户消息额度高于助手，长消息保留开头+结尾
    这保证任意规模的对话都不会拼接出超长 prompt（历史 bug：长对话全量拼接
    导致 LLM 只关注尾部、摘要遗漏早期主题）。
    """
    if not messages:
        return None

    # 如果没有 LLM 客户端，使用简单摘要
    if not llm_client:
        return simple_structured_summary(messages, input_budget=input_budget)

    # ---- 1. 输入预算控制：最新优先裁剪 ----
    budget = input_budget if input_budget else DEFAULT_SUMMARY_INPUT_BUDGET
    kept: List[Message] = []
    dropped = 0
    budget_tokens = 0
    for msg in reversed(messages):
        tok = estimate_message_tokens(msg)
        if budget_tokens + tok > budget and kept:
            dropped += 1
        else:
            budget_tokens += tok
            kept.insert(0, msg)
    # 至少保留最后一条消息，避免空输入
    if not kept:
        kept = [messages[-1]]
        dropped = max(0, len(messages) - 1)

    # ---- 2. 自适应截断：位置越新额度越高，用户消息优先 ----
    content_parts = []
    n = len(kept)
    for i, msg in enumerate(kept):
        role = "用户" if msg.role == "user" else "助手" if msg.role == "assistant" else msg.role
        pos_ratio = (i + 1) / n  # 0..1，越大越新
        if msg.role == "user":
            char_budget = int(600 + pos_ratio * 1400)  # 600..2000
        elif msg.role == "assistant":
            char_budget = int(300 + pos_ratio * 700)   # 300..1000
        else:
            char_budget = 400
        content_parts.append(f"{role}: {_summarize_message_content(msg, char_budget)}")

    conversation_content = "\n\n".join(content_parts)
    if dropped > 0:
        conversation_content = (
            f"[注：为控制摘要输入规模，最旧的 {dropped} 条消息未纳入本次摘要，"
            f"但其主题仍应在摘要中覆盖]\n\n{conversation_content}"
        )

    # ---- 3. 获取提示词 ----
    if direction:
        prompt = get_partial_compact_prompt(custom_instructions, direction)
    else:
        prompt = get_compact_prompt(custom_instructions)

    # 构建完整请求
    full_prompt = f"{prompt}\n\n对话历史：\n{conversation_content}"

    try:
        response = llm_client.chat.completions.create(
            model=llm_model or "gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": full_prompt
                }
            ],
            max_tokens=8000,  # 输出预算充足，避免摘要被截断（历史 bug：3000 tokens 截断导致无 </summary>）
            temperature=0.3
        )

        summary = response.choices[0].message.content
        logger.info(f"[ContextCompressor] Generated structured summary: {len(summary)} chars")

        # 验证摘要格式是否包含必需的标题
        valid, missing = _validate_summary_format(summary)
        if not valid:
            logger.warning(f"[ContextCompressor] Summary format invalid, missing sections: {missing}. Retrying with stricter prompt...")
            retry_summary = _retry_summary_with_strict_prompt(
                llm_client=llm_client,
                llm_model=llm_model,
                conversation_content=conversation_content,
                direction=direction,
                custom_instructions=custom_instructions
            )
            if retry_summary:
                valid2, missing2 = _validate_summary_format(retry_summary)
                if valid2:
                    return retry_summary
                logger.warning(f"[ContextCompressor] Retry also failed format validation, missing: {missing2}")
            # 格式校验失败，用强制包裹确保前端可解析
            logger.warning("[ContextCompressor] Falling back to wrapped format")
            return _force_wrap_summary(summary)

        return summary

    except Exception as e:
        logger.warning(f"[ContextCompressor] Failed to generate summary: {e}")
        return simple_structured_summary(messages, input_budget=input_budget)


def simple_structured_summary(messages: List[Message], input_budget: Optional[int] = None) -> str:
    """
    简单结构化摘要（无 LLM 时使用）

    输出与 LLM 摘要同构的 ## 格式（前端 CompactSummaryCard 可直接解析），
    覆盖全部用户消息（按 input_budget 预算截断，最新优先），并自动提取
    工具名与文件路径——修复历史缺陷：原实现只取最近 3-5 条用户消息且
    无 ## 标题，导致 fallback 摘要内容严重残缺、前端显示 (none)。
    """
    if not messages:
        return ""

    budget = input_budget if input_budget else DEFAULT_SUMMARY_INPUT_BUDGET

    # 提取全部用户消息（最新优先预算裁剪）
    user_texts = []
    for msg in messages:
        if msg.role != "user":
            continue
        content = msg.content
        if isinstance(content, str):
            user_texts.append(content)
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", "") or "")
            user_texts.append("\n".join(parts))

    kept_users = []
    used = 0
    for t in reversed(user_texts):
        c = min(len(t), 300)
        if used + c > budget:
            break
        used += c
        kept_users.insert(0, t)
    dropped_users = len(user_texts) - len(kept_users)

    def _snippet(text: str, limit: int) -> str:
        s = text.strip().replace('\n', ' ')
        return s if len(s) <= limit else s[:limit] + "..."

    # 提取工具名
    tool_names = []
    for msg in messages:
        if msg.role == "assistant" and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name and name not in tool_names:
                        tool_names.append(name)

    # 提取文件路径（Windows 盘符路径 + 项目内相对路径）
    file_paths = []
    path_re = re.compile(
        r'([A-Za-z]:[\\/][^\s"\'<>|?*]+'
        r'|(?:src|ui-gui-vue3|gateway|agent_core|tools|extensions|skills|agents|knowledgesource)[\\/][^\s"\'<>|?*]+)'
    )
    for msg in messages:
        if isinstance(msg.content, str):
            text = msg.content
        elif isinstance(msg.content, list):
            text = " ".join((b.get("text", "") or "") if isinstance(b, dict) else "" for b in msg.content)
        else:
            text = str(msg.content)
        for m in path_re.finditer(text):
            fp = m.group(1).replace('\\', '/')
            if fp not in file_paths:
                file_paths.append(fp)
        if len(file_paths) >= 10:
            break

    goal = _snippet(kept_users[0], 150) if kept_users else "(none)"

    parts = ["<summary>"]
    parts.append("## Goal")
    parts.append(f"- {goal}")
    parts.append("")
    parts.append("## Constraints & Preferences")
    parts.append("- (none)")
    parts.append("")
    parts.append("## Progress")
    parts.append("### Done")
    if tool_names:
        for tn in tool_names[:15]:
            parts.append(f"- 使用了工具 {tn}")
    else:
        parts.append("- (none)")
    parts.append("### In Progress")
    parts.append("- (none)")
    parts.append("### Blocked")
    parts.append("- (none)")
    parts.append("")
    parts.append("## Key Decisions")
    parts.append("- (none)")
    parts.append("")
    parts.append("## Next Steps")
    parts.append("- 继续之前的任务")
    parts.append("")
    parts.append("## Critical Context")
    parts.append(f"- 对话共 {len(user_texts)} 条用户消息；本次摘要由规则生成（无 LLM 支持）")
    for i, t in enumerate(kept_users[:5]):
        parts.append(f"- 用户消息: {_snippet(t, 100)}")
    if dropped_users > 0:
        parts.append(f"- 最旧的 {dropped_users} 条用户消息因预算未纳入，详见完整会话记录")
    parts.append("")
    parts.append("## Relevant Files")
    if file_paths:
        for fp in file_paths[:10]:
            parts.append(f"- {fp}")
    else:
        parts.append("- (none)")
    parts.append("")
    parts.append("</summary>")
    return "\n".join(parts)


def create_post_compact_file_attachments(
    file_states: Dict[str, FileState],
    max_files: int = POST_COMPACT_MAX_FILES_TO_RESTORE
) -> List[Dict[str, Any]]:
    """
    创建压缩后要恢复的文件附件
    
    这防止模型需要重新读取最近访问的文件
    """
    # 按时间戳排序
    sorted_files = sorted(
        file_states.items(),
        key=lambda x: x[1].timestamp,
        reverse=True
    )[:max_files]
    
    attachments = []
    used_tokens = 0
    
    for path, state in sorted_files:
        content = truncate_to_tokens(state.content, POST_COMPACT_MAX_TOKENS_PER_FILE)
        tokens = rough_token_count_estimation(content)
        
        if used_tokens + tokens > POST_COMPACT_TOKEN_BUDGET:
            continue
        
        used_tokens += tokens
        attachments.append({
            "type": "file_content",
            "path": path,
            "content": content,
        })
    
    return attachments


# ============================================================================
# 兼容旧 API 的包装函数
# ============================================================================

def compress_context(
    messages: List[Dict[str, str]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    recent_messages: int = DEFAULT_RECENT_MESSAGES,
    llm_client = None,
    llm_model: str = None
) -> List[Dict[str, str]]:
    """
    压缩上下文（兼容旧 API）
    
    Args:
        messages: 消息列表（字典格式）
        max_tokens: 最大 Token 数
        recent_messages: 保留的最近消息数
        llm_client: OpenAI 客户端
        llm_model: 使用的模型
    
    Returns:
        压缩后的消息列表（字典格式）
    """
    # 转换为 Message 对象
    msg_objects = []
    for m in messages:
        msg_objects.append(Message(
            uuid=str(uuid.uuid4()),
            role=m.get("role", "user"),
            content=m.get("content", ""),
            reasoning_content=m.get("reasoning_content"),
            tool_call_id=m.get("tool_call_id"),
            tool_calls=m.get("tool_calls")
        ))
    
    # 执行压缩
    result = compact_conversation(
        messages=msg_objects,
        llm_client=llm_client,
        llm_model=llm_model,
        max_tokens=max_tokens,
        recent_messages=recent_messages
    )
    
    if not result.success:
        return messages
    
    # 转换回字典格式
    output = []
    for msg in [result.boundary_marker] + result.summary_messages + result.messages_to_keep:
        if msg:
            output.append(msg.to_dict())
    
    return output


def smart_compress(
    messages: List[Dict[str, str]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    strategy: str = "smart",
    llm_client = None,
    llm_model: str = None
) -> List[Dict[str, str]]:
    """
    智能压缩（兼容旧 API）
    
    Args:
        messages: 消息列表
        max_tokens: 最大 Token 数
        strategy: 压缩策略 ('smart', 'recent', 'summary', 'none')
        llm_client: OpenAI 客户端
        llm_model: 使用的模型
    
    Returns:
        压缩后的消息列表
    """
    if strategy == "none":
        return messages
    
    current_tokens = estimate_messages_tokens([
        Message(uuid=str(uuid.uuid4()), role=m.get("role", "user"), content=m.get("content", ""))
        for m in messages
    ])
    
    if current_tokens <= max_tokens:
        return messages
    
    if strategy == "recent":
        # 只保留最近的消息
        result = []
        tokens = 0
        for msg in reversed(messages):
            msg_tokens = estimate_tokens(msg.get("content", "")) + 4
            if tokens + msg_tokens > max_tokens:
                break
            result.insert(0, msg)
            tokens += msg_tokens
        return result
    
    elif strategy == "summary":
        # 生成摘要
        return compress_context(messages, max_tokens, 10, llm_client, llm_model)
    
    else:  # smart
        # 智能选择策略
        msg_count = len(messages)
        
        if msg_count <= 20:
            return messages[-15:]
        elif msg_count <= 50:
            return messages[-30:]
        else:
            return compress_context(messages, max_tokens, 20, llm_client, llm_model)


def manual_compress(
    messages: List[Dict[str, str]],
    max_tokens: int = 50000,
    recent_messages: int = 20,
    llm_client = None,
    llm_model: str = None
) -> Dict[str, Any]:
    """
    手动压缩上下文（兼容旧 API）
    
    返回详细的压缩结果
    """
    # 转换为 Message 对象
    msg_objects = []
    for m in messages:
        msg_objects.append(Message(
            uuid=str(uuid.uuid4()),
            role=m.get("role", "user"),
            content=m.get("content", ""),
            reasoning_content=m.get("reasoning_content"),
            tool_call_id=m.get("tool_call_id"),
            tool_calls=m.get("tool_calls")
        ))
    
    # 执行压缩
    result = compact_conversation(
        messages=msg_objects,
        llm_client=llm_client,
        llm_model=llm_model,
        max_tokens=max_tokens,
        recent_messages=recent_messages,
        trigger=CompactTrigger.MANUAL
    )
    
    # 转换结果
    if not result.success:
        return {
            "success": False,
            "reason": result.error,
            "before": {"tokens": result.pre_compact_token_count, "messages": len(messages)},
            "after": {"tokens": result.pre_compact_token_count, "messages": len(messages)},
            "saved_percent": 0,
            "summary": None,
            "compressed_messages": messages
        }
    
    # 转换压缩后的消息
    compressed_messages = []
    for msg in [result.boundary_marker] + result.summary_messages + result.messages_to_keep:
        if msg:
            compressed_messages.append(msg.to_dict())
    
    return {
        "success": True,
        "reason": None,
        "before": {"tokens": result.pre_compact_token_count, "messages": len(messages)},
        "after": {"tokens": result.post_compact_token_count, "messages": len(compressed_messages)},
        "saved_percent": result.saved_percent,
        "summary": result.summary,
        "compressed_messages": compressed_messages
    }


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    # 核心类
    'Message',
    'CompactBoundaryMessage',
    'CompactionResult',
    'CompactTrigger',
    'CompactDirection',
    'FileState',
    'SkillState',
    'PlanFile',
    
    # Token 计算
    'estimate_tokens',
    'estimate_message_tokens',
    'estimate_messages_tokens',
    
    # 提示词
    'get_compact_prompt',
    'get_partial_compact_prompt',
    'format_compact_summary',
    'get_compact_user_summary_message',
    
    # 图片/文档剥离
    'strip_images_from_messages',
    'strip_reinjected_attachments',
    
    # Tool pair 保护
    'adjust_index_to_preserve_api_invariants',
    'get_tool_result_ids',
    'has_tool_use_with_ids',
    
    # 消息分组 / PTL 检测
    'group_messages_by_api_round',
    'is_prompt_too_long_error',
    
    # 压缩边界
    'create_compact_boundary_message',
    'annotate_boundary_with_preserved_segment',
    'is_compact_boundary_message',
    'get_messages_after_compact_boundary',
    
    # Plan/Skill 保留
    'PlanManager',
    'create_plan_attachment_if_needed',
    'create_skill_attachment_if_needed',
    'truncate_to_tokens',
    
    # 核心压缩函数
    'compact_conversation',
    'generate_structured_summary',
    'create_post_compact_file_attachments',
    
    # 兼容旧 API
    'compress_context',
    'smart_compress',
    'manual_compress',
    'simple_structured_summary',
]
