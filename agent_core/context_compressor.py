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
import threading
import uuid
import json
import time
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

# 导入 Transcript 管理器
from .transcript_manager import (
    get_transcript_manager,
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
    """压缩触发类型"""
    MANUAL = "manual"


class CompactDirection(Enum):
    """部分压缩方向"""
    FROM = "from"  # 从指定位置向后压缩
    UP_TO = "up_to"  # 压缩到指定位置


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


# ============================================================================
# 1. 结构化摘要提示词
# ============================================================================

# 压缩摘要：只输出文本，按固定标题交接

COMPACT_PREAMBLE = """Reply with text only. Do not call any tool: tool calls are refused and
would consume the single output opportunity this request has.

This is a reformatting job, not engineering work. The conversation below already
holds everything you need. Read it, extract the facts, write the handoff, stop.
Do not analyse the code, do not propose improvements, do not write an essay, and
do not keep deliberating once you know what happened: a long deliberation spends
the output budget and leaves nothing for the answer itself.
"""

COMPACT_METHOD = """Rules:
1. Walk the conversation in chronological order and cover every topic, not just the latest part.
2. Keep verifiable details: file paths, commands, error text, function names, identifiers.
3. Do not copy code blocks. Say which file was touched and what kind of change it was.
4. Do not describe the summarizing process and do not mention that the context was compacted.
5. Use the five headings below exactly, in this order, without adding or removing sections.
6. The headings stay in English. The section bodies are written in {language}.
7. If something is genuinely unknown, write "unknown" once instead of guessing or restating the question.
"""

COMPACT_TEMPLATE = """## Task
- One sentence on what is being worked on.

## Status
- Done: ...
- In progress: ...
- Blocked: ...

## Decisions
- Key trade-offs and the reason behind them.

## Todo
- Next steps, in order.

## Clues
- Key files, commands, error text, constraints worth remembering.
"""

COMPACT_TASK_FULL = "Turn the whole conversation into a handoff summary the next session can resume from directly."
COMPACT_TASK_FROM = "Summarize only the recent stretch of the conversation; earlier content is kept as-is and needs no recap."
COMPACT_TASK_UP_TO = "Summarize the conversation so far; the summary will sit at the head of a later session that cannot see these messages."


def _build_compact_prompt(
    task: str,
    custom_instructions: Optional[str] = None,
    language: str = "English"
) -> str:
    prompt = (
        f"{COMPACT_PREAMBLE}\n{task}\n\n"
        f"{COMPACT_METHOD.format(language=language)}\n\n"
        f"{COMPACT_TEMPLATE}"
    )
    if custom_instructions and custom_instructions.strip():
        prompt += f"\nAdditional requirements:\n{custom_instructions}"
    return prompt


BASE_COMPACT_PROMPT = _build_compact_prompt(COMPACT_TASK_FULL)
PARTIAL_COMPACT_FROM_PROMPT = _build_compact_prompt(COMPACT_TASK_FROM)
PARTIAL_COMPACT_UP_TO_PROMPT = _build_compact_prompt(COMPACT_TASK_UP_TO)


def get_compact_prompt(
    custom_instructions: Optional[str] = None,
    language: str = "English"
) -> str:
    """Full compaction prompt, with section bodies in `language`."""
    return _build_compact_prompt(COMPACT_TASK_FULL, custom_instructions, language)


def get_partial_compact_prompt(
    custom_instructions: Optional[str] = None,
    direction: CompactDirection = CompactDirection.FROM,
    language: str = "English"
) -> str:
    """Partial compaction prompt, with section bodies in `language`."""
    task = COMPACT_TASK_UP_TO if direction == CompactDirection.UP_TO else COMPACT_TASK_FROM
    return _build_compact_prompt(task, custom_instructions, language)


def format_compact_summary(summary: str) -> str:
    """清理摘要文本：去掉历史格式遗留的分析块与包裹标签。"""
    formatted = re.sub(r'<analysis>[\s\S]*?</analysis>', '', summary or '')
    formatted = re.sub(r'</?summary>', '', formatted)
    formatted = re.sub(r'^Summary:\s*\n', '', formatted.strip())
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

    base_summary = f"""本次会话承接此前的对话，以下是早前内容的交接摘要。

{formatted_summary}"""

    if transcript_path:
        base_summary += f"\n\n需要早前的完整细节（代码片段、错误原文、生成内容）时，可读取：{transcript_path}"

    if recent_messages_preserved:
        base_summary += "\n\n最近的消息保持原样。"

    if suppress_follow_up_questions:
        return f"""{base_summary}
直接从上次中断处继续，不要向用户追问，也不要复述摘要或说明自己正在继续。"""

    return base_summary


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
        tokens = estimate_tokens(truncated_content)
        
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
    """截断内容到指定 token 数量，保留头部

    字符预算按 estimate_tokens 降级公式的中英文比例反推。
    """
    if estimate_tokens(content) <= max_tokens:
        return content
    
    truncation_marker = "\n\n[... content truncated for compaction; read the full file if needed]"
    # 字符预算：按中英文比例估算每 token 字符数（与 estimate_tokens 降级公式一致）
    total_chars = len(content)
    chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
    if total_chars:
        chinese_ratio = chinese_chars / total_chars
        chars_per_token = 1.5 * chinese_ratio + 4 * (1 - chinese_ratio)
    else:
        chars_per_token = 4
    char_budget = max(int(max_tokens * chars_per_token) - len(truncation_marker), 0)
    return content[:char_budget] + truncation_marker


# ============================================================================
# 8. PTL 错误检测
# ============================================================================


# Providers spell the same rejection differently. `context_length_exceeded` and
# "Input too long: N input tokens, limit is M" are the openai-compatible gateway
# forms; missing them left the 11-09 failure classified as a generic error, so
# the real token count in the message was never read.
_PTL_MARKERS = (
    "prompt_too_long",
    "context length",
    "context_length_exceeded",
    "input too long",
    "maximum context",
)


def is_prompt_too_long_error(error: Any) -> Tuple[bool, Optional[int], Optional[int]]:
    """
    检查是否是 prompt_too_long 错误
    
    Returns:
        (是否是 PTL 错误, token gap, 模型的上下文限制 token 数)
        token_gap 和 context_limit 在无法解析时为 None
    """
    error_str = str(error).lower()

    if not any(marker in error_str for marker in _PTL_MARKERS):
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

    # 格式2b: "[context_length_exceeded] Input too long: 1636417 input tokens,
    # limit is 1048576 for this model" — the openai-compatible gateway form that
    # the 11-09 incident produced.
    if limit is None:
        match = re.search(r'input too long:?\s*(\d+)\s*input tokens?,?\s*limit is (\d+)', error_str)
        if match:
            actual = int(match.group(1))
            limit = int(match.group(2))

    # 格式2c: the same numbers without the phrasing, e.g. "1636417 input tokens
    # ... limit is 1048576"
    if limit is None:
        match = re.search(r'(\d+)\s*input tokens', error_str)
        limit_match = re.search(r'limit is (\d+)', error_str)
        if match and limit_match:
            actual = int(match.group(1))
            limit = int(limit_match.group(1))

    # 格式3: 提示词超长（X tokens > Y tokens）
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

# How much of the final turn survives a compaction. The unit is a whole turn,
# not a message count: one request plus its assistants and tool calls stays
# together, capped so a turn that ran fifty tools cannot swallow the context.
MAX_KEPT_TURN_MESSAGES = 20


def _trailing_turn(dialog_messages: List[Message]) -> List[Message]:
    """Return the last complete turn: newest user message through the end.

    What survives compaction should be one intact unit of work. Slicing by a raw
    message count breaks that unit in tool-heavy sessions, where ninety percent of
    the rows are tool results and "keep the newest two" keeps two shell echoes.
    """
    if not dialog_messages:
        return []
    start = 0
    for i in range(len(dialog_messages) - 1, -1, -1):
        if dialog_messages[i].role == "user":
            start = i
            break
    kept = dialog_messages[start:]
    # At least one message has to be summarized, or compaction does nothing.
    # Dropping the first message keeps the work that followed it, instead of
    # collapsing a single-turn conversation down to its last tool echo.
    if len(kept) >= len(dialog_messages):
        kept = dialog_messages[1:]
    if len(kept) > MAX_KEPT_TURN_MESSAGES:
        kept = kept[-MAX_KEPT_TURN_MESSAGES:]
    return kept



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
    context_limit: Optional[int] = None,
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
    logger.info(
        f"[ContextCompressor] Manual compress requested: {pre_compact_token_count} tokens, "
        f"budget {'auto (from model window)' if not max_tokens else max_tokens}"
    )
    
    # 4. 分离系统消息和对话消息
    system_messages = [m for m in messages if m.role == "system"]
    dialog_messages = [m for m in messages if m.role != "system"]
    
    # 5. 检查对话消息数量（至少 2 条才能压缩：压缩 >=1 条、保留 >=1 条）
    if len(dialog_messages) < 2:
        return CompactionResult(
            success=False,
            error=f"Not enough messages to compact ({len(dialog_messages)} < 2).",
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
                error="No messages to compact.",
                pre_compact_token_count=pre_compact_token_count,
                trigger=trigger
            )
    else:
        # 标准压缩：保留最近消息（动态保留数：最多保留 recent_messages 条；
        # 标准压缩：保留最后一轮完整往返（用户的最后一条消息，以及它触发的
        # 全部助手回复和工具调用）。工具密集的一轮里按条数保留 N 条，留下的
        # 只是几条工具回显，等于把一整段完整的工作折叠掉。
        messages_to_keep = _trailing_turn(dialog_messages)
        messages_to_summarize = dialog_messages[:len(dialog_messages) - len(messages_to_keep)]
    
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
                error="No messages to compact: tool-call pairing does not allow splitting here.",
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
        input_budget=max_tokens if max_tokens else None,
        context_limit=context_limit,
    )
    
    if not summary:
        return CompactionResult(
            success=False,
            error="Summary generation failed; the session was left untouched.",
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
# Required section headings. Kept in English so the structure stays language
# independent: only the section bodies follow the conversation's language.
_REQUIRED_SECTIONS = [
    '## Task',
    '## Status',
    '## Decisions',
    '## Todo',
    '## Clues',
]

# A body made of headings and placeholders is not a summary. Below this much
# real text the output is treated as a failed generation, never persisted. The
# floor is low on purpose: a single-line Chinese handoff is still a handoff, and
# a heading-only shell measures zero here.
_MIN_SUMMARY_CHARS = 30
_PLACEHOLDER_RE = re.compile(r'\(\s*none\s*\)|^\s*[-*+]\s*\.\.\.\s*$', re.IGNORECASE | re.MULTILINE)
_EMPTY_LABEL_RE = re.compile(
    r'^\s*[-*+]?\s*(?:Done|In progress|Blocked|已完成|进行中|阻塞)\s*[:：]\s*$',
    re.IGNORECASE | re.MULTILINE
)


def _summary_text_volume(summary: str) -> int:
    """Count real content characters, ignoring headings, bullets and placeholders."""
    text = re.sub(r'^\s{0,3}#{1,6}\s+.*$', '', summary or '', flags=re.MULTILINE)
    text = _PLACEHOLDER_RE.sub('', text)
    text = text.replace('...', '').replace('…', '')
    text = _EMPTY_LABEL_RE.sub('', text)
    text = re.sub(r'^\s*[-*+]\s*', '', text, flags=re.MULTILINE)
    return len(re.sub(r'\s+', '', text))


def _validate_summary_format(summary: str) -> tuple:
    """
    Check the output carries the required headings and enough real content.
    Returns (ok, missing_headings, content_chars).
    At least 3/5 headings plus `_MIN_SUMMARY_CHARS` of body text are required:
    a heading-only shell passes the first test but fails the second.
    """
    content = re.sub(r'<analysis>[\s\S]*?</analysis>', '', summary or '')
    content = re.sub(r'</?summary>', '', content)

    found = []
    missing = []
    for section in _REQUIRED_SECTIONS:
        if re.search(re.escape(section), content, re.IGNORECASE):
            found.append(section)
        else:
            missing.append(section)

    volume = _summary_text_volume(content)
    return (len(found) >= 3 and volume >= _MIN_SUMMARY_CHARS), missing, volume


def _retry_summary_with_strict_prompt(
    llm_client,
    llm_model: str,
    conversation_content: str,
    direction=None,
    custom_instructions=None,
    language: str = "English",
    context_limit: int = 0,
) -> Optional[str]:
    """Second attempt with an explicit, non-negotiable heading contract.

    Routed through `_call_summary_model` like every other summary request, so it
    cannot drift away from the shared preamble, the size handling or the
    calibration feed.
    """
    if direction == CompactDirection.FROM:
        task = COMPACT_TASK_FROM
    elif direction == CompactDirection.UP_TO:
        task = COMPACT_TASK_UP_TO
    else:
        task = COMPACT_TASK_FULL

    strict_prefix = (
        "The previous answer was rejected: it either missed required headings or "
        "carried no real content. Answer again, following the contract.\n"
        "Use exactly these five headings, spelled and ordered as shown, kept in English:\n"
        "## Task / ## Status / ## Decisions / ## Todo / ## Clues\n"
        "Every heading must appear. Write each body in "
        f"{language}. Use (none) only for a section that is truly empty, never for "
        "the whole document. No preamble, no closing line, no tool calls.\n"
    )
    prompt = f"{strict_prefix}\n{_build_compact_prompt(task, custom_instructions, language)}"

    try:
        summary = _call_summary_model(
            llm_client, llm_model,
            f"{prompt}\n\nConversation:\n{conversation_content}",
            _summary_output_budget(context_limit or FALLBACK_CONTEXT_LIMIT),
            0.1,
        )
        logger.info(f"[ContextCompressor] Retry summary: {len(summary)} chars")
        return summary
    except Exception as e:
        logger.warning(f"[ContextCompressor] Retry failed: {e}")
        return None


def _force_wrap_summary(raw: str) -> str:
    """Last resort: keep what the model wrote, filed under the first heading.

    This preserves text instead of discarding it, but the result may still be a
    shell. Callers must check `_summary_text_volume` and refuse to persist one.
    """
    content = re.sub(r'<analysis>[\s\S]*?</analysis>', '', raw or '')
    content = re.sub(r'</?summary>', '', content).strip()
    # Drop headings the model did emit, so the body is not duplicated.
    content = re.sub(r'^\s{0,3}#{1,6}\s+.*$', '', content, flags=re.MULTILINE).strip()
    snippet = content[:4000] if content else ""
    return f"""## Task
- {snippet or "(none)"}

## Status
- Done: (none)
- In progress: (none)
- Blocked: (none)

## Decisions
- (none)

## Todo
- (none)

## Clues
- (none)"""


# Summary input budget is derived from the model window (`_summary_input_budget`);
# there is no fixed ceiling to fall back on any more.

# Output ceiling for the summary call itself. Generous on purpose: a reasoning
# model spends part of this before it writes a word, and the failure mode of a
# tight ceiling is an empty answer, not a shorter one. Shrinks to a quarter of
# the window on small models (`_summary_output_budget`), because a provider
# checks input + max_tokens together.
SUMMARY_MAX_OUTPUT_TOKENS = 90000

# Ceiling for one part's digest during the map phase. Generous on purpose: a
# reasoning model spends part of this budget before it writes a single word of
# the digest, and a tight ceiling shows up as an empty answer rather than a
# shorter one.
SUMMARY_PART_OUTPUT_TOKENS = 20000

# One request may not carry more than this, even when the model window is larger.
# Splitting is cheap; a request that takes minutes and then fails is not.
SUMMARY_PART_INPUT_TOKENS = 120_000

# Prompt scaffolding (instructions plus the part header) around the transcript.
SUMMARY_PROMPT_OVERHEAD_TOKENS = 4_000

# Share of the window kept free so rounding and format overhead cannot push a
# request over the limit.
CONTEXT_SAFETY_RATIO = 0.10

# Assumed window when the model is unknown.
FALLBACK_CONTEXT_LIMIT = 128_000

# Multiplier applied to an uncalibrated estimate. `estimate_tokens` reads English
# at ~4 chars per token; real mixed code/JSON/Chinese runs closer to 2.5, and
# pessimism only costs extra parts while optimism costs the whole compaction.
UNCALIBRATED_PESSIMISM = 1.6

# Per-message rendering caps. A single tool result used to reach the request at
# full length, so one 2 MB grep could fill every part on its own.
RENDER_USER_CHARS = 4000
RENDER_ASSISTANT_CHARS = 4000
RENDER_TOOL_CHARS = 4000
RENDER_TOOL_COMMAND_CHARS = 8000
RENDER_TOOL_READ_CHARS = 600
RENDER_TOOL_EDIT_CHARS = 800

# How many part digests may be merged into one before the merge must recurse.
SUMMARY_MERGE_FANIN = 8

# Calibration: measured/estimated ratios observed so far. Bounded and median
# based, so one odd request cannot redefine the scale.
_CALIBRATION_MAX_SAMPLES = 24
_CALIBRATION_MIN_RATIO = 0.4
_CALIBRATION_MAX_RATIO = 4.0
_calibration_samples: List[float] = []
_calibration_lock = threading.Lock()


def record_token_calibration(estimated: int, actual: int) -> None:
    """Feed one measured/estimated pair into the scale.

    `actual` comes from a provider `prompt_tokens` (or a rejection message) and
    `estimated` from `estimate_tokens` over the same text. The caller decides
    which text; the only requirement is that both numbers describe it.
    """
    try:
        if not actual or actual <= 0 or not estimated or estimated <= 0:
            return
        ratio = actual / estimated
        if not (_CALIBRATION_MIN_RATIO <= ratio <= _CALIBRATION_MAX_RATIO):
            return
        with _calibration_lock:
            _calibration_samples.append(ratio)
            if len(_calibration_samples) > _CALIBRATION_MAX_SAMPLES:
                del _calibration_samples[:-_CALIBRATION_MAX_SAMPLES]
    except Exception as e:
        logger.debug(f"[ContextCompressor] Calibration sample rejected: {e}")


def get_token_calibration_factor() -> Optional[float]:
    """Median measured/estimated ratio, or None before any sample landed."""
    with _calibration_lock:
        samples = list(_calibration_samples)
    if not samples:
        return None
    samples.sort()
    mid = len(samples) // 2
    if len(samples) % 2:
        return samples[mid]
    return (samples[mid - 1] + samples[mid]) / 2


def estimate_tokens_calibrated(text: str) -> int:
    """Token estimate corrected for this provider's real tokenizer.

    With samples the median ratio is applied; without them the pessimistic
    multiplier is. Never returns less than the plain estimate.
    """
    base = estimate_tokens(text)
    if base <= 0:
        return 0
    factor = get_token_calibration_factor()
    scale = factor if factor else UNCALIBRATED_PESSIMISM
    return max(base, int(base * scale))


def resolve_context_limit(model_name: Optional[str]) -> int:
    """Model window in tokens: pricing table first, name matching second."""
    try:
        from .agent_chat_service_v2 import get_model_context_limit
        limit = int(get_model_context_limit(model_name or "") or 0)
        if limit > 0:
            return limit
    except Exception as e:
        logger.debug(f"[ContextCompressor] Context limit lookup failed: {e}")
    return FALLBACK_CONTEXT_LIMIT


def _summary_output_budget(context_limit: int) -> int:
    """Output ceiling for one summary call on this window.

    `SUMMARY_MAX_OUTPUT_TOKENS` is the ceiling the user asked for, but a provider
    checks input + max_tokens together. Reserving the whole ceiling on a small
    window would leave almost no room for the transcript, so it shrinks to a
    quarter of the window there.
    """
    limit = int(context_limit or 0)
    if limit <= 0:
        return SUMMARY_MAX_OUTPUT_TOKENS
    return max(4_000, min(SUMMARY_MAX_OUTPUT_TOKENS, int(limit * 0.25)))


def _part_output_budget(context_limit: int, retry: bool = False) -> int:
    """Output ceiling for one part digest; doubled on a retry, still capped."""
    ceiling = _summary_output_budget(context_limit)
    base = min(SUMMARY_PART_OUTPUT_TOKENS, ceiling)
    return min(ceiling, base * 2) if retry else base


def _summary_input_budget(context_limit: int) -> int:
    """Largest transcript a single summary request may carry."""
    usable = int(context_limit * (1 - CONTEXT_SAFETY_RATIO)) - _summary_output_budget(context_limit)
    usable -= SUMMARY_PROMPT_OVERHEAD_TOKENS
    if usable <= 0:
        # Window smaller than the output ceiling: fall back to a fraction of it
        # so the request still has a chance rather than being impossible.
        usable = max(8_000, int(context_limit * 0.4) - SUMMARY_PROMPT_OVERHEAD_TOKENS)
    return min(usable, SUMMARY_PART_INPUT_TOKENS)


# Tool families, decided by name when the transcript is rendered for the summary.
# Reads are re-derivable (the file is still on disk), edits only need their
# shape, and commands carry evidence that exists nowhere else.
_READ_TOOLS = {
    'read_file', 'ls', 'list_directory', 'glob', 'grep', 'search_files',
    'search_in_file', 'find_files', 'get_file_info', 'todo_read',
    'get_env_var', 'get_system_info', 'list_processes',
    'lsp_document_symbol', 'lsp_hover', 'lsp_workspace_symbol',
    'lsp_go_to_definition', 'lsp_find_references', 'lsp_go_to_implementation',
    'lsp_incoming_calls', 'lsp_outgoing_calls', 'lsp_prepare_call_hierarchy',
}
_EDIT_TOOLS = {
    'write_file', 'edit_file', 'delete_file', 'move_file', 'copy_file',
    'create_directory', 'todo_write',
}
# Command tools keep more of their output: shell results, test runs and stack
# traces are the only copy of what happened. The cap still applies.
_COMMAND_TOOLS = {'execute_shell', 'powershell', 'execute_code', 'bash', 'batch'}

# Below this size an output is short enough that a failure marker is the whole
# payload rather than incidental text inside a file that was read successfully.
_TOOL_FAILURE_SNIFF_CHARS = 500

_FAILURE_MARKERS = re.compile(
    r'(^\s*error\b|traceback \(most recent call last\)|\bexit code [1-9]|'
    r'\bfailed\b|\bpermission denied\b|\brefused\b|\bcannot\b)',
    re.IGNORECASE | re.MULTILINE
)


def _looks_failed(text: str) -> bool:
    """Best-effort failure detection on plain tool output.

    Tool results carry no structured status, so the text is the only signal. It
    errs towards "failed": a false positive costs a few tokens, a false negative
    loses the error text that nothing else preserves.
    """
    if not text:
        return False
    head = text[:1200]
    if _FAILURE_MARKERS.search(head):
        return True
    stripped = head.lstrip()
    return stripped.startswith('Error') or stripped.startswith('Traceback')


def _truncate(text: str, limit: int) -> str:
    """Head slice with an explicit marker, so the model knows it is partial."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f" [... {len(text) - limit} chars omitted ...]"


def _summarize_message_content(
    msg: Message,
    char_budget: int,
    tool_name: Optional[str] = None
) -> str:
    """Render one message as plain text for the summary prompt.

    Human messages keep a head+tail slice so both the ask and its outcome
    survive. Tool messages are graded by family: reads shrink to a stub, edits
    keep only the shape of the change, commands keep more because their output
    is the only record of what ran. Every family is capped, including unknown
    names and failures: falling through to the whole body is how one 2 MB grep
    filled the entire request on its own.
    """
    if msg.role == "tool":
        body = _message_plain_text(msg)
        name = tool_name or "tool"
        # A read's body is file content, so the word "error" inside a source file
        # says nothing about the read itself. Failure sniffing is therefore
        # limited to commands and edits, plus any short response where an error
        # would be the whole payload.
        structured = name not in _READ_TOOLS
        failed = _looks_failed(body) and (structured or len(body) <= _TOOL_FAILURE_SNIFF_CHARS)
        if name in _READ_TOOLS:
            cap = RENDER_TOOL_READ_CHARS
        elif name in _EDIT_TOOLS:
            cap = RENDER_TOOL_EDIT_CHARS
        elif name in _COMMAND_TOOLS:
            cap = RENDER_TOOL_COMMAND_CHARS
        else:
            cap = RENDER_TOOL_CHARS
        rendered = _truncate(body, cap)
        return f"[{name}] FAILED\n{rendered}" if failed else f"[{name}]\n{rendered}"

    text = _message_plain_text(msg)
    if not text:
        return ""
    if len(text) <= char_budget:
        return text
    marker = "\n[... %d chars omitted ...]\n" % (len(text) - char_budget)
    usable = char_budget - len(marker)
    if usable < 50:
        usable = max(char_budget // 2, 10)
    head_len = int(usable * 0.6)
    tail_len = usable - head_len
    return text[:head_len] + marker + text[-tail_len:]


def _message_plain_text(msg: Message) -> str:
    """Best-effort plain text of a message, whatever shape its content has."""
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        parts = []
        for block in msg.content:
            b = block if isinstance(block, dict) else getattr(block, '__dict__', {})
            if b.get("type") == "text":
                parts.append(b.get("text") or "")
        return "\n".join(parts)
    return "" if msg.content is None else str(msg.content)


def _tool_name_index(messages: List[Message]) -> Dict[str, str]:
    """Map tool_call_id -> tool name, rebuilt from the assistant turns.

    A tool message only carries the call id; the name lives on the assistant
    message that requested the call. Rebuilding the index here keeps the Message
    class untouched and still lets the renderer tell a read from a shell run.
    """
    index: Dict[str, str] = {}
    for msg in messages:
        for call in (msg.tool_calls or []):
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            name = None
            # OpenAI shape: {"id": ..., "function": {"name": ...}}
            fn = call.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
            if not name:
                name = call.get("name")
            # Orbit block shape: {"type": "tool", "tool_info": {"id", "name"}}.
            # The transcript stores this form, so without reading it every tool
            # rendered as an unknown name.
            info = call.get("tool_info")
            if isinstance(info, dict):
                call_id = call_id or info.get("id")
                name = name or info.get("name")
            if call_id and name:
                index[call_id] = name
    return index


def _detect_body_language(messages: List[Message]) -> str:
    """Decide the summary body language from what the user actually wrote.

    Fixed here instead of delegated to the model: a model told to "answer in the
    user's language" drifts between languages mid-document, and the reader of the
    card is the person whose messages set the tone. Sampling walks the newest
    messages backwards, because the tail is what the next session resumes from.
    """
    sample: List[str] = []
    sampled = 0
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        text = _message_plain_text(msg).strip()
        if not text:
            continue
        sample.append(text[:2000])
        sampled += len(sample[-1])
        if sampled >= 6000:
            break
    joined = "".join(sample)
    cjk = len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', joined))
    latin = len(re.findall(r'[A-Za-z]', joined))
    total = cjk + latin
    if total == 0:
        return "English"
    # A fifth of the letters being CJK is already Chinese in practice; below
    # that, an English sentence carrying a Chinese term stays English.
    return "Chinese" if cjk / total >= 0.2 else "English"


class _PromptTooLongError(Exception):
    """The provider rejected a request because it exceeded the model window."""

    def __init__(self, actual: Optional[int], limit: Optional[int], message: str = ""):
        self.actual = actual
        self.limit = limit
        super().__init__(message or f"prompt too long: actual={actual} limit={limit}")


def extract_actual_prompt_tokens(error: Any) -> Optional[int]:
    """Read the provider's own token count for the rejected prompt.

    `is_prompt_too_long_error` answers "was this a size rejection" and the model
    limit; the actual count is what makes a retry calculable, so it is parsed
    separately here rather than widening that function's contract.
    """
    try:
        error_str = str(error).lower()
        patterns = (
            r'input too long:?\s*(\d+)\s*input tokens',
            r'you requested (\d+) tokens',
            r'prompt is too long:?\s*(\d+)\s*tokens?\s*>',
            r'(\d+)\s*tokens?\s*>\s*\d+',
        )
        for pattern in patterns:
            match = re.search(pattern, error_str)
            if match:
                return int(match.group(1))
    except Exception:
        pass
    return None


def _split_rendered_parts(rows: List[str], budget_tokens: int) -> List[str]:
    """Pack rendered rows into request-sized parts, preserving order.

    Greedy on purpose: a boundary may fall inside a turn, which is acceptable
    because each part is digested on its own and the merge restores the
    through-line. What matters is that every row lands in exactly one part.
    """
    budget_tokens = max(4_000, int(budget_tokens))
    parts: List[str] = []
    current: List[str] = []
    used = 0
    for row in rows:
        cost = estimate_tokens_calibrated(row) + 8
        if current and used + cost > budget_tokens:
            parts.append("\n\n".join(current))
            current = []
            used = 0
        current.append(row)
        used += cost
    if current:
        parts.append("\n\n".join(current))
    return parts


def _part_prompt(index: int, total: int, language: str) -> str:
    """Instruction for digesting one part of a long transcript."""
    return (
        f"You are writing part {index} of {total} of a factual handoff record for a "
        f"long conversation that is about to be compacted.\n"
        f"Write in {language}. Record only what the next session needs:\n"
        f"- what the user was trying to do\n"
        f"- decisions made, and the reason given\n"
        f"- files touched, commands run, exact error text\n"
        f"- what is still open\n"
        f"No greetings, no meta commentary, no headings. Dense lines. "
        f"Never repeat a fact twice."
    )


def _merge_prompt(language: str) -> str:
    """Instruction for folding several digests into one record."""
    return (
        f"Merge the digests below into a single factual record, written in {language}. "
        f"Keep every distinct fact: goals, decisions, file paths, commands, error "
        f"text, open items. Drop duplication and narrative. "
        f"No headings, no meta commentary, no preamble."
    )


def _call_summary_model(
    llm_client: Any,
    llm_model: Optional[str],
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """One summary request.

    The preamble is attached here rather than in each prompt builder, so no
    builder can lose the "reformatting job, do not deliberate" framing.

    Returns the answer. Raises `_PromptTooLongError` when the provider rejected
    it for size, so the caller can measure and split; any other failure is
    re-raised untouched. A successful call feeds its measured `prompt_tokens`
    back into the calibration scale, which is what makes the next estimate
    trustworthy.
    """
    # Single choke point: every summary call passes through here, so the
    # preamble is attached once here instead of in each prompt builder. A
    # builder that forgot it (the part digests did) lets the model treat this
    # as engineering work, spend the output ceiling deliberating, and answer
    # with nothing.
    if COMPACT_PREAMBLE not in prompt:
        prompt = f"{COMPACT_PREAMBLE}\n{prompt}"

    try:
        response = llm_client.chat.completions.create(
            model=llm_model or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as e:
        too_long, _gap, limit = is_prompt_too_long_error(e)
        if too_long:
            raise _PromptTooLongError(extract_actual_prompt_tokens(e), limit, str(e)) from e
        raise
    usage = getattr(response, "usage", None)
    measured = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    if measured > 0:
        record_token_calibration(estimate_tokens(prompt), measured)
    try:
        message = response.choices[0].message
        content = message.content or ""
    except (AttributeError, IndexError, TypeError):
        return ""
    if not content.strip():
        # An empty answer is diagnostic, not just absent: on a reasoning model it
        # usually means the ceiling went to the reasoning trace.
        reasoning = getattr(message, "reasoning_content", None) or ""
        logger.warning(
            f"[ContextCompressor] Summary call returned empty content "
            f"(reasoning_chars={len(reasoning)}, max_tokens={max_tokens})"
        )
    return content


def _summarize_part_digest(
    part_text: str,
    index: int,
    total: int,
    llm_client: Any,
    llm_model: Optional[str],
    language: str,
    budget_tokens: int,
    context_limit: int,
    depth: int = 0,
    attempt: int = 0,
) -> Optional[str]:
    """Digest one part, splitting further when the provider says it is too long.

    A size rejection carries the real token count, so the part is re-split with
    a budget derived from that measurement instead of the estimate that let it
    through. Depth is capped: two halvings turn any part into something a
    provider will take, and a third would mean the content itself is unusable.
    """
    prompt = f"{_part_prompt(index, total, language)}\n\nTranscript part:\n{part_text}"
    try:
        text = _call_summary_model(
            llm_client, llm_model, prompt,
            _part_output_budget(context_limit, retry=attempt > 0), 0.3 if attempt == 0 else 0.2
        ).strip()
    except _PromptTooLongError as e:
        actual = e.actual
        local = estimate_tokens(prompt)
        if actual:
            record_token_calibration(local, actual)
        if depth >= 2:
            logger.error(
                f"[ContextCompressor] Part {index}/{total} rejected for size twice; giving up"
            )
            return None
        ratio = (actual / local) if (actual and local) else 2.0
        rows = [row for row in part_text.split("\n\n") if row]
        if len(rows) <= 1:
            logger.error(
                f"[ContextCompressor] Part {index}/{total} is one row and cannot be split"
            )
            return None
        smaller = max(2_000, int(budget_tokens / max(2.0, ratio)))
        sub_parts = _split_rendered_parts(rows, smaller)
        if len(sub_parts) <= 1:
            return None
        digests: List[str] = []
        for sub_index, sub_text in enumerate(sub_parts, 1):
            digest = _summarize_part_digest(
                sub_text, sub_index, len(sub_parts), llm_client, llm_model,
                language, smaller, context_limit, depth + 1,
            )
            if not digest:
                return None
            digests.append(digest)
        return "\n\n".join(digests)
    except Exception as e:
        logger.warning(f"[ContextCompressor] Part {index}/{total} failed: {e}")
        return None

    if not text:
        if attempt == 0:
            # A reasoning model can spend the whole ceiling thinking and answer
            # with nothing; that says nothing about the content. Ask again with
            # room to spare before calling the part a failure.
            logger.warning(
                f"[ContextCompressor] Part {index}/{total} came back empty; "
                f"retrying with a larger output ceiling"
            )
            return _summarize_part_digest(
                part_text, index, total, llm_client, llm_model, language,
                budget_tokens, context_limit, depth, 1,
            )
        logger.warning(f"[ContextCompressor] Part {index}/{total} came back empty twice")
        return None
    return text


def _merge_part_digests(
    digests: List[str],
    llm_client: Any,
    llm_model: Optional[str],
    language: str,
    budget_tokens: int,
) -> Optional[str]:
    """Fold part digests until one request can hold them.

    Each round merges groups of `SUMMARY_MERGE_FANIN`, so the digest count falls
    by that factor and the loop terminates in a handful of rounds even for a
    transcript split into dozens of parts. Returning the joined text that still
    exceeds the budget is intentional: the final call has its own size handling
    and a partial record beats no record.
    """
    current = [d for d in digests if d]
    if not current:
        return None
    for _ in range(6):
        joined = "\n\n---\n\n".join(current)
        if estimate_tokens_calibrated(joined) <= budget_tokens:
            return joined
        merged: List[str] = []
        for start in range(0, len(current), SUMMARY_MERGE_FANIN):
            group = current[start:start + SUMMARY_MERGE_FANIN]
            if len(group) == 1:
                merged.append(group[0])
                continue
            body = "\n\n---\n\n".join(group)
            prompt = f"{_merge_prompt(language)}\n\nDigests:\n{body}"
            try:
                text = _call_summary_model(
                    llm_client, llm_model, prompt, SUMMARY_PART_OUTPUT_TOKENS, 0.2
                ).strip()
            except Exception as e:
                logger.warning(f"[ContextCompressor] Digest merge failed: {e}")
                return None
            if not text:
                logger.warning("[ContextCompressor] Digest merge came back empty")
                return None
            merged.append(text)
        current = merged
        if len(current) == 1:
            break
    return "\n\n---\n\n".join(current)


def _ensure_usable_summary(candidate: str) -> Optional[str]:
    """Return a summary worth persisting, or None.

    Headings alone are not a summary. Persisting a shell replaces real history
    with nothing, which is worse than reporting the failure: the transcript stays
    intact and the user can compact again.
    """
    ok, missing, volume = _validate_summary_format(candidate)
    if ok:
        return candidate
    logger.warning(
        f"[ContextCompressor] Summary rejected: missing={missing}, content_chars={volume}"
    )
    return None


def generate_structured_summary(
    messages: List[Message],
    llm_client: Any = None,
    llm_model: str = None,
    custom_instructions: Optional[str] = None,
    direction: Optional[CompactDirection] = None,
    input_budget: Optional[int] = None,
    language: Optional[str] = None,
    context_limit: Optional[int] = None,
) -> Optional[str]:
    """
    Build the five-section handoff summary.

    The transcript is rendered once, capped per message, then packed into parts
    that fit a single request. A transcript that fits is summarized in one call;
    a longer one is digested part by part and the digests are merged, so length
    decides how many calls it takes, never whether it works. The part budget comes
    from the model window and the calibrated token estimate, not a constant.

    Bodies are written in `language` (detected from the user's messages when not
    given); headings stay English so the structure is stable.

    Returns None when nothing usable could be produced. Callers must treat that
    as a failure and leave the transcript untouched: there is no rule-built
    fallback any more, because a shell is worse than a visible failure.
    """
    if not messages:
        return None

    body_language = language or _detect_body_language(messages)

    if not llm_client:
        # No model means no summary. A rule-built shell is not a handoff, and
        # persisting one replaces real history with nothing.
        logger.error("[ContextCompressor] No LLM client; refusing to compact")
        return None

    # ---- 1. 按模型窗口决定单次请求的预算 ----
    if context_limit and context_limit > 0:
        window = int(context_limit)
    else:
        window = resolve_context_limit(llm_model)
    if input_budget and input_budget > 0:
        part_budget = min(int(input_budget), SUMMARY_PART_INPUT_TOKENS)
    else:
        part_budget = _summary_input_budget(window)

    # ---- 2. 逐条渲染，每条独立封顶 ----
    name_by_call_id = _tool_name_index(messages)
    rows: List[str] = []
    for msg in messages:
        if msg.role == "user":
            row_budget = RENDER_USER_CHARS
            label = "User"
        elif msg.role == "assistant":
            row_budget = RENDER_ASSISTANT_CHARS
            label = "Assistant"
        else:
            row_budget = RENDER_TOOL_CHARS
            label = "Tool"
        tool_name = name_by_call_id.get(msg.tool_call_id or "") if msg.role == "tool" else None
        body = _summarize_message_content(msg, row_budget, tool_name)
        if body:
            rows.append(f"{label}: {body}")

    if not rows:
        logger.error("[ContextCompressor] Nothing renderable to summarize")
        return None

    # ---- 3. 切块：装不下就分段，绝不整段硬发 ----
    parts = _split_rendered_parts(rows, part_budget)
    logger.info(
        f"[ContextCompressor] Summary planned: {len(rows)} rows, {len(parts)} part(s), "
        f"budget {part_budget} tokens, window {window}, "
        f"output {_summary_output_budget(window)}, "
        f"calibration {get_token_calibration_factor() or 'none'}"
    )

    if len(parts) == 1:
        conversation_content = parts[0]
    else:
        digests: List[str] = []
        for index, part_text in enumerate(parts, 1):
            digest = _summarize_part_digest(
                part_text, index, len(parts), llm_client, llm_model,
                body_language, part_budget, window,
            )
            if not digest:
                logger.error(
                    f"[ContextCompressor] Part {index}/{len(parts)} produced no digest; "
                    f"refusing to persist a partial summary"
                )
                return None
            digests.append(digest)
        merged_digest = _merge_part_digests(
            digests, llm_client, llm_model, body_language, part_budget
        )
        if not merged_digest:
            logger.error("[ContextCompressor] Digest merge produced nothing; refusing to compact")
            return None
        conversation_content = merged_digest
        logger.info(
            f"[ContextCompressor] Digested {len(parts)} parts into "
            f"{len(conversation_content)} chars"
        )

    # ---- 3. 获取提示词 ----
    if direction:
        prompt = get_partial_compact_prompt(custom_instructions, direction, body_language)
    else:
        prompt = get_compact_prompt(custom_instructions, body_language)

    # ---- 4. 生成五段式摘要；被拒时按实测比例缩减后重试一次 ----
    final_content = conversation_content
    summary = ""
    for attempt in range(2):
        full_prompt = f"{prompt}\n\nConversation:\n{final_content}"
        try:
            summary = _call_summary_model(
                llm_client, llm_model, full_prompt, SUMMARY_MAX_OUTPUT_TOKENS, 0.3
            )
            break
        except _PromptTooLongError as e:
            # The provider named the real size. Shrink by that measurement rather
            # than the estimate that let the request through, then try once more.
            actual = e.actual
            local = estimate_tokens(full_prompt)
            if actual:
                record_token_calibration(local, actual)
            if attempt == 1:
                logger.error(
                    f"[ContextCompressor] Final summary request rejected for size "
                    f"(actual={e.actual} limit={e.limit}); compaction reported as failed"
                )
                return None
            ratio = (actual / local) if (actual and local) else 2.0
            keep = int(len(final_content) / max(2.0, ratio) * 0.85)
            if keep < 2000:
                logger.error(
                    f"[ContextCompressor] Final summary request rejected for size and the "
                    f"digest cannot shrink further (actual={e.actual}); reporting failure"
                )
                return None
            final_content = final_content[:keep]
            logger.warning(
                f"[ContextCompressor] Final summary request too long "
                f"(actual={actual or '?'} vs limit {e.limit or '?'}); "
                f"retrying with {keep} chars"
            )
        except Exception as e:
            logger.warning(f"[ContextCompressor] Failed to generate summary: {e}")
            return None

    logger.info(f"[ContextCompressor] Generated structured summary: {len(summary)} chars")
    try:

        ok, missing, volume = _validate_summary_format(summary)
        if ok:
            return summary

        logger.warning(
            f"[ContextCompressor] Summary rejected: missing={missing}, "
            f"content_chars={volume}. Retrying with a stricter prompt..."
        )
        retry_summary = _retry_summary_with_strict_prompt(
            llm_client=llm_client,
            llm_model=llm_model,
            conversation_content=conversation_content,
            direction=direction,
            custom_instructions=custom_instructions,
            language=body_language,
            context_limit=window,
        )
        # Keep whichever attempt actually said something: the first answer may be
        # empty while the retry carried real content.
        candidate = retry_summary or summary
        ok2, missing2, volume2 = _validate_summary_format(candidate)
        if ok2:
            return candidate
        logger.warning(
            f"[ContextCompressor] Retry rejected too: missing={missing2}, "
            f"content_chars={volume2}"
        )

        wrapped = _force_wrap_summary(candidate)
        usable = _ensure_usable_summary(wrapped)
        if not usable:
            logger.error(
                "[ContextCompressor] No usable summary from the model; "
                "compaction will be reported as failed"
            )
        return usable

    except Exception as e:
        logger.warning(f"[ContextCompressor] Failed to finalize summary: {e}")
        return None


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
        tokens = estimate_tokens(content)
        
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

def _messages_from_dicts(messages: List[Dict[str, Any]]) -> List[Message]:
    """字典消息 → Message 对象（逐条生成 uuid）。"""
    return [
        Message(
            uuid=str(uuid.uuid4()),
            role=m.get("role", "user"),
            content=m.get("content", ""),
            reasoning_content=m.get("reasoning_content"),
            tool_call_id=m.get("tool_call_id"),
            tool_calls=m.get("tool_calls"),
        )
        for m in messages
    ]


def _compacted_messages_to_dicts(result: CompactionResult) -> List[Dict[str, Any]]:
    """压缩结果中的消息序列 → 字典列表。"""
    return [
        msg.to_dict()
        for msg in [result.boundary_marker] + result.summary_messages + result.messages_to_keep
        if msg
    ]


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
    msg_objects = _messages_from_dicts(messages)
    
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
    return _compacted_messages_to_dicts(result)


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
    max_tokens: Optional[int] = None,
    recent_messages: int = 20,
    llm_client = None,
    llm_model: str = None,
    session_id: Optional[str] = None,
    project_path: Optional[str] = None,
    context_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    手动压缩上下文（兼容旧 API）
    
    返回详细的压缩结果
    """
    # 转换为 Message 对象
    msg_objects = _messages_from_dicts(messages)
    
    # 执行压缩
    result = compact_conversation(
        messages=msg_objects,
        llm_client=llm_client,
        llm_model=llm_model,
        max_tokens=max_tokens,
        recent_messages=recent_messages,
        trigger=CompactTrigger.MANUAL,
        session_id=session_id,
        project_path=project_path,
        context_limit=context_limit,
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
    compressed_messages = _compacted_messages_to_dicts(result)
    
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

    # 尺寸校准
    'record_token_calibration',
    'get_token_calibration_factor',
    'estimate_tokens_calibrated',
    'resolve_context_limit',
]
