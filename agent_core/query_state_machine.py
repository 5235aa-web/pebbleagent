"""
Query State Machine - 查询状态机

- State: 跨迭代传递的可变状态
- Continue: 表示继续循环
- Terminal: 表示终止循环

核心设计原则：
1. 协程 yield 实现流式输出
2. Continue/Terminal 控制循环流向
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


# ==================== 状态机核心类型 ====================

@dataclass
class ContinueReason:
    """继续循环的原因"""
    type: str
    details: Optional[Dict[str, Any]] = None


class ContinueType(Enum):
    """继续循环的类型"""
    TOOL_USE = "tool_use"           # 有工具调用需要执行
    MAX_OUTPUT_TOKENS = "max_output_tokens"  # 输出 token 超限，需要继续
    RECOVERY = "recovery"           # 错误恢复后继续
    COMPACT = "compact"             # 压缩后继续


@dataclass
class Continue:
    """
    继续循环信号
    
    表示循环应该继续，并携带下一轮迭代需要的状态更新
    """
    reason: ContinueType
    details: Optional[Dict[str, Any]] = None
    
    @classmethod
    def tool_use(cls, tool_calls: List[ToolUseBlock]) -> 'Continue':
        """创建工具调用继续信号"""
        return cls(
            reason=ContinueType.TOOL_USE,
            details={"tool_calls": tool_calls}
        )
    
    @classmethod
    def max_output_tokens(cls, recovery_count: int) -> 'Continue':
        """创建 max_output_tokens 恢复信号"""
        return cls(
            reason=ContinueType.MAX_OUTPUT_TOKENS,
            details={"recovery_count": recovery_count}
        )
    
    @classmethod
    def recovery(cls, error_type: str) -> 'Continue':
        """创建错误恢复信号"""
        return cls(
            reason=ContinueType.RECOVERY,
            details={"error_type": error_type}
        )
    
    @classmethod
    def compact(cls, saved_tokens: int) -> 'Continue':
        """创建压缩后继续信号"""
        return cls(
            reason=ContinueType.COMPACT,
            details={"saved_tokens": saved_tokens}
        )


@dataclass
class TerminalReason:
    """终止循环的原因"""
    type: str
    details: Optional[Dict[str, Any]] = None


class TerminalType(Enum):
    """终止循环的类型"""
    END_TURN = "end_turn"           # 正常结束
    BLOCKING_LIMIT = "blocking_limit"  # 达到阻塞限制
    CANCELLED = "cancelled"         # 用户取消
    ERROR = "error"                 # 错误终止


@dataclass
class Terminal:
    """
    终止循环信号
    
    表示循环应该终止，并携带终止原因
    """
    reason: TerminalType
    details: Optional[Dict[str, Any]] = None
    
    @classmethod
    def blocking_limit(cls, token_count: int) -> 'Terminal':
        """创建阻塞限制信号"""
        return cls(
            reason=TerminalType.BLOCKING_LIMIT,
            details={"token_count": token_count}
        )
    
    @classmethod
    def cancelled(cls, message: str = "User cancelled") -> 'Terminal':
        """创建取消信号"""
        return cls(
            reason=TerminalType.CANCELLED,
            details={"message": message}
        )
    
    @classmethod
    def error(cls, error_message: str) -> 'Terminal':
        """创建错误终止信号"""
        return cls(
            reason=TerminalType.ERROR,
            details={"error_message": error_message}
        )


# ==================== 循环状态定义 ====================

@dataclass
class QueryState:
    """
    查询循环状态
    
    跨迭代传递的可变状态，每次迭代开始时解构，
    Continue 站点更新整个状态对象
    """
    # 消息列表
    messages: List[ChatMessage]
    
    # 上下文
    system_prompt: str
    user_context: Dict[str, str]
    
    # 追踪信息
    turn_count: int = 1
    query_chain_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    query_depth: int = 0
    
    # 恢复状态
    max_output_tokens_recovery_count: int = 0
    
    # 当前迭代的工具调用
    pending_tool_calls: List[ToolUseBlock] = field(default_factory=list)
    
    # 当前迭代的响应文本
    current_response_text: str = ""
    
    # 累积的所有响应文本
    accumulated_response_text: str = ""
    
    # 执行顺序（用于正确显示穿插的文字和工具）
    execution_order: List[Dict[str, Any]] = field(default_factory=list)
    
    # 工具执行结果
    tool_executions: List[Dict[str, Any]] = field(default_factory=list)
    
    # 取消信号
    cancel_requested: bool = False
    
    def clone(self) -> 'QueryState':
        """创建状态的深拷贝"""
        return QueryState(
            messages=self.messages.copy(),
            system_prompt=self.system_prompt,
            user_context=self.user_context.copy(),
            turn_count=self.turn_count,
            query_chain_id=self.query_chain_id,
            query_depth=self.query_depth,
            max_output_tokens_recovery_count=self.max_output_tokens_recovery_count,
            pending_tool_calls=self.pending_tool_calls.copy(),
            current_response_text=self.current_response_text,
            accumulated_response_text=self.accumulated_response_text,
            execution_order=self.execution_order.copy(),
            tool_executions=self.tool_executions.copy(),
            cancel_requested=self.cancel_requested,
        )
    
    def increment_turn(self) -> 'QueryState':
        """增加轮数"""
        self.turn_count += 1
        return self
    
    def add_message(self, message: ChatMessage) -> 'QueryState':
        """添加消息"""
        self.messages.append(message)
        return self
    
    def add_response_text(self, text: str) -> 'QueryState':
        """添加响应文本"""
        self.current_response_text = text
        self.accumulated_response_text += text
        return self
    
    def add_execution_item(self, item: Dict[str, Any]) -> 'QueryState':
        """添加执行项"""
        self.execution_order.append(item)
        return self
    
    def reset_for_next_iteration(self) -> 'QueryState':
        """为下一次迭代重置状态"""
        self.current_response_text = ""
        self.pending_tool_calls = []
        return self


# ==================== 循环结果类型 ====================

LoopResult = Union[Continue, Terminal]


# ==================== 查询配置 ====================

@dataclass
class QueryConfig:
    """查询配置"""
    max_output_tokens_recovery_limit: int = 3
    max_tool_concurrency: int = 10
    enable_streaming_tool_execution: bool = True


# ==================== 工具执行上下文 ====================

@dataclass
class ToolExecutionContext:
    """工具执行上下文"""
    conversation_uuid: str
    agent_name: str
    project_path: str
    cancel_event: asyncio.Event
    state: QueryState
    
    # 工具执行回调
    on_tool_start: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None
    on_tool_complete: Optional[Callable[[str, str, int], Awaitable[None]]] = None
    on_tool_error: Optional[Callable[[str, Exception], Awaitable[None]]] = None
    
    # 快照系统上下文字段
    snapshot_session_uuid: Optional[str] = None
    snapshot_message_id: Optional[int] = None
    snapshot_tool_call_id: Optional[str] = None


# ==================== 查询循环抽象基类 ====================

class QueryLoop(ABC):
    """
    查询循环抽象基类
    
    定义状态机循环的核心接口
    """
    
    def __init__(self, config: QueryConfig = None):
        self.config = config or QueryConfig()
        self._cancel_signals: Dict[str, asyncio.Event] = {}
    
    @abstractmethod
    async def run(
        self, 
        initial_state: QueryState,
        context: ToolExecutionContext
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        运行查询循环
        
        Args:
            initial_state: 初始状态
            context: 工具执行上下文
        
        Yields:
            流式事件
        """
        pass
    
    def request_cancel(self, conversation_uuid: str):
        """请求取消"""
        if conversation_uuid in self._cancel_signals:
            self._cancel_signals[conversation_uuid].set()
    
    def _create_cancel_signal(self, conversation_uuid: str) -> asyncio.Event:
        """创建取消信号"""
        event = asyncio.Event()
        self._cancel_signals[conversation_uuid] = event
        return event
    
    def _remove_cancel_signal(self, conversation_uuid: str):
        """移除取消信号"""
        if conversation_uuid in self._cancel_signals:
            del self._cancel_signals[conversation_uuid]


# ==================== 辅助函数 ====================

def create_initial_state(
    system_prompt: str,
    messages: List[Dict[str, str]] = None,
    user_context: Dict[str, str] = None
) -> QueryState:
    """
    创建初始状态
    
    Args:
        system_prompt: 系统提示词
        messages: 初始消息列表
        user_context: 用户上下文
    
    Returns:
        初始状态对象
    """
    chat_messages = []
    if messages:
        for i, msg in enumerate(messages):
            chat_messages.append(ChatMessage(
                uuid=str(uuid.uuid4()),
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id")
            ))
    
    return QueryState(
        messages=chat_messages,
        system_prompt=system_prompt,
        user_context=user_context or {}
    )


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
