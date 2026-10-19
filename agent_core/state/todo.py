"""
任务清单工具 - 任务列表管理

提供任务列表管理功能
支持实时事件推送到前端
"""
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
import time

logger = logging.getLogger(__name__)


class TodoStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TodoItem:
    """任务项"""
    id: str
    content: str
    status: TodoStatus = TodoStatus.PENDING
    priority: str = "medium"
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status.value,
            "priority": self.priority,
            "created_at": self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TodoItem":
        return cls(
            id=data["id"],
            content=data["content"],
            status=TodoStatus(data.get("status", "pending")),
            priority=data.get("priority", "medium"),
            created_at=data.get("created_at", time.time())
        )


class TodoManager:
    """任务管理器（单例模式）- 支持事件推送"""
    
    _instance: Optional["TodoManager"] = None
    _todos: Dict[str, List[TodoItem]] = {}
    _current_session_id: str = "default"
    _event_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    _ws_push_callback: Optional[Callable[[str, dict], None]] = None  # (event_type, payload)
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def set_event_callback(cls, callback: Callable[[List[Dict[str, Any]]], None]) -> None:
        """设置事件回调函数，当任务列表更新时调用"""
        cls._event_callback = callback
        logger.info("[TodoManager] Event callback registered")
    
    @classmethod
    def register_ws_push(cls, push_fn: Callable[[str, dict], None]) -> None:
        """注册 WebSocket 推送回调（供 ws_server 注入）。
        当 _event_callback 为 None 时（agent 直接调用工具），
        通过此回调将事件推送到前端。
        
        Args:
            push_fn: async def push_fn(event_type: str, payload: dict)
        """
        cls._ws_push_callback = push_fn
        logger.info("[TodoManager] WS push callback registered")
    
    @classmethod
    def get_todos(cls, session_id: Optional[str] = None) -> List[TodoItem]:
        """获取指定会话的任务列表"""
        sid = session_id or cls._current_session_id
        return cls._todos.get(sid, [])
    
    @classmethod
    def set_todos(cls, todos: List[TodoItem], session_id: Optional[str] = None) -> None:
        """设置指定会话的任务列表"""
        sid = session_id or cls._current_session_id
        cls._todos[sid] = todos
    
    @classmethod
    def update_todos(cls, todos_data: List[Dict[str, Any]], session_id: Optional[str] = None) -> tuple:
        """
        更新任务列表
        
        Returns:
            (old_todos, new_todos) 返回旧的和新的任务列表
        """
        sid = session_id or cls._current_session_id
        old_todos = cls.get_todos(sid)
        
        new_todos = []
        for item_data in todos_data:
            if isinstance(item_data, dict):
                todo = TodoItem.from_dict(item_data)
                new_todos.append(todo)
        
        cls.set_todos(new_todos, sid)
        
        # 触发事件回调，通知前端
        todos_dict = [t.to_dict() for t in new_todos]
        
        # 优先使用 chat_stream 内的回调（LLM 调用路径）
        if cls._event_callback:
            try:
                cls._event_callback(todos_dict)
                logger.info(f"[TodoManager] Event triggered via chat_stream callback: {len(new_todos)} todos")
            except Exception as e:
                logger.error(f"[TodoManager] Event callback error: {e}")
        # fallback：通过 WebSocket 直接推送（agent 直接调用 todo_write 的路径）
        elif cls._ws_push_callback:
            try:
                import asyncio
                payload = {"todos": todos_dict}
                if asyncio.iscoroutinefunction(cls._ws_push_callback):
                    # 异步推送：在后台线程中执行
                    import threading
                    def _push():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(cls._ws_push_callback("todos.update", payload))
                        finally:
                            loop.close()
                    threading.Thread(target=_push, daemon=True).start()
                else:
                    cls._ws_push_callback("todos.update", payload)
                logger.info(f"[TodoManager] Event triggered via WS push: {len(new_todos)} todos")
            except Exception as e:
                logger.error(f"[TodoManager] WS push error: {e}")
        
        return old_todos, new_todos
    
    @classmethod
    def set_session_id(cls, session_id: str) -> None:
        """设置当前会话ID"""
        cls._current_session_id = session_id
    
    @classmethod
    def clear_todos(cls, session_id: Optional[str] = None) -> None:
        """清空任务列表"""
        sid = session_id or cls._current_session_id
        if sid in cls._todos:
            del cls._todos[sid]
        
        # 触发事件回调，通知前端清空
        if cls._event_callback:
            try:
                cls._event_callback([])
                logger.info("[TodoManager] Event triggered: todos cleared")
            except Exception as e:
                logger.error(f"[TodoManager] Event callback error: {e}")

