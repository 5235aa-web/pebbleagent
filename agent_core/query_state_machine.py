"""
Query State Machine - 查询状态机（事件与上下文类型）

- StreamEvent 家族: 流式事件类型（ToolExecutionEvent 等）
- ToolUseBlock / ChatMessage: 消息与工具调用结构
- ToolExecutionContext: 工具执行上下文（各执行器共享）

注（2026-10-16）：原 Continue/Terminal/QueryState/QueryConfig/QueryLoop
状态机 DSL 全仓零调用（主循环为 chat_stream 内手写实现），已移除。
"""

from dataclasses import dataclass, field
from typing import (
    List, Dict, Any, Optional, Union, 
    AsyncGenerator, Callable, Awaitable, Tuple
)
from enum import Enum
import uuid
import time
import asyncio
import json
import logging
from abc import ABC, abstractmethod

from .safe_json import safe_json_loads

logger = logging.getLogger(__name__)


# ==================== 消息类型定义 ====================

class MessageType(Enum):
    """消息类型枚举"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    COMPACT_BOUNDARY = "compact_boundary"


@dataclass
class ChatMessage:
    """聊天消息"""
    uuid: str
    role: str
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    
    def to_api_format(self) -> Dict[str, Any]:
        """转换为 API 格式"""
        result = {"role": self.role, "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        return result


@dataclass
class ToolUseBlock:
    """工具调用块"""
    id: str
    name: str
    arguments: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments
            }
        }


# ==================== 流式事件定义 ====================

@dataclass
class StreamEvent:
    """流式事件基类"""
    type: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ContentEvent(StreamEvent):
    """内容流式事件"""
    content: str = ""
    iteration: int = 0
    
    def __post_init__(self):
        self.type = "content"


@dataclass
class ToolExecutionEvent(StreamEvent):
    """工具执行事件"""
    tool_execution_id: str = ""
    tool_name: str = ""
    status: str = ""  # "running", "completed", "failed"
    input: Optional[str] = None
    output: Optional[str] = None
    duration: Optional[int] = None
    iteration: int = 0
    
    def __post_init__(self):
        self.type = "tool_execution"


@dataclass
class CompactEvent(StreamEvent):
    """压缩事件"""
    reason: str = ""
    trigger: str = ""  # "auto", "reactive", "manual"
    
    def __post_init__(self):
        self.type = "compact_triggered"


@dataclass
class ChatStartedEvent(StreamEvent):
    """聊天开始事件"""
    agent_name: str = ""
    conversation_uuid: str = ""
    
    def __post_init__(self):
        self.type = "chat_started"


@dataclass
class ChatCompletedEvent(StreamEvent):
    """聊天完成事件"""
    content: str = ""
    agent_name: str = ""
    
    def __post_init__(self):
        self.type = "chat_completed"


@dataclass
class ErrorEvent(StreamEvent):
    """错误事件"""
    message: str = ""
    
    def __post_init__(self):
        self.type = "error"


@dataclass
class CancelledEvent(StreamEvent):
    """取消事件"""
    message: str = ""
    
    def __post_init__(self):
        self.type = "cancelled"


# ==================== 工具执行上下文 ====================

@dataclass
class ToolExecutionContext:
    """工具执行上下文"""
    conversation_uuid: str
    agent_name: str
    project_path: str
    cancel_event: asyncio.Event
    
    # 工具执行回调
    on_tool_start: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None
    on_tool_complete: Optional[Callable[[str, str, int], Awaitable[None]]] = None
    on_tool_error: Optional[Callable[[str, Exception], Awaitable[None]]] = None
    
    # 快照系统上下文字段
    snapshot_session_uuid: Optional[str] = None
    snapshot_message_id: Optional[int] = None
    snapshot_tool_call_id: Optional[str] = None


def parse_tool_calls_from_response(
    tool_calls_chunks: Dict[int, Dict[str, Any]]
) -> List[ToolUseBlock]:
    """
    从流式响应中解析工具调用
    
    Args:
        tool_calls_chunks: 工具调用块字典
    
    Returns:
        工具调用列表
    """
    tool_calls = []
    for idx in sorted(tool_calls_chunks.keys()):
        chunk = tool_calls_chunks[idx]
        
        # 解析参数（使用安全解析）
        args_str = chunk.get('function', {}).get('arguments', '{}')
        args = safe_json_loads(args_str, default={}) if isinstance(args_str, str) else args_str
        
        name = chunk.get('function', {}).get('name', '')
        if not name:
            logger.warning(f"[QueryStateMachine] Tool call with empty name at index {idx}, skipping")
            continue
        
        tool_calls.append(ToolUseBlock(
            id=chunk.get('id', str(uuid.uuid4())),
            name=name,
            arguments=args
        ))
    
    return tool_calls
