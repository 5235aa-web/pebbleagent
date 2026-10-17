"""
Query State Machine - 查询状态机（事件与上下文类型）

- StreamEvent / ToolExecutionEvent: 流式工具执行事件
- ToolUseBlock: 工具调用结构
- ToolExecutionContext: 工具执行上下文（各执行器共享）
"""

from dataclasses import dataclass, field
from typing import (
    Dict, Any, Optional, Callable, Awaitable
)
import time
import asyncio
import logging

logger = logging.getLogger(__name__)


# ==================== 工具调用结构 ====================

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
