"""
TodoWrite工具 - 任务列表管理

参照Claude Code的TodoWriteTool实现，提供任务列表管理功能
支持实时事件推送到前端
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
import json

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


class TodoWriteTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    任务列表写入工具
    
    用于管理会话任务列表，支持任务的增删改查
    """
    
    name = "todo_write"
    description = """Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.

When to Use This Tool:
- Complex multi-step tasks: When a task requires 3+ distinct steps or actions
- Non-trivial and complex tasks: Tasks that require careful planning and multiple operations
- User explicitly requests todo list: When the user directly asks you to use the todo list
- User provides multiple tasks: When users provide a list of things to be done (numbered or comma-separated)
- After receiving new instructions: Immediately capture user requirements as todos
- When starting work on a task: Mark it as in_progress BEFORE beginning work. Ideally, only one todo should be in_progress at a time
- After completing a task: Mark it as completed and add any new follow-up tasks discovered during implementation

When NOT to Use This Tool:
- Single, straightforward tasks: When there is only one trivial task to do
- Purely conversational or informational exchanges

Task States:
- pending: Task not yet started
- in_progress: Currently working on (limit to ONE task at a time)
- completed: Task finished successfully"""
    
    input_params = ["todos"]
    requires_permission = False
    danger_level = "low"
    
    parameters_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "List of todo items to set",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique identifier for the todo item (auto-generated if not provided)"
                        },
                        "content": {
                            "type": "string",
                            "description": "The task description"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": "Current status of the task"
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "Priority level of the task"
                        }
                    },
                    "required": ["content"]
                }
            }
        },
        "required": ["todos"]
    }
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        todos = kwargs.get("todos")
        if todos is None:
            raise ValueError("todos parameter is required")
        
        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        
        import uuid
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                raise ValueError(f"todo item {i} must be a dictionary")
            if "content" not in item:
                raise ValueError(f"todo item {i} must have 'content' field")
            # id 字段可选，未提供时自动生成
            if "id" not in item:
                item["id"] = f"todo_{uuid.uuid4().hex[:8]}"
            
            status = item.get("status", "pending")
            if status not in ["pending", "in_progress", "completed"]:
                raise ValueError(f"todo item {i} has invalid status: {status}")
            
            priority = item.get("priority", "medium")
            if priority not in ["high", "medium", "low"]:
                raise ValueError(f"todo item {i} has invalid priority: {priority}")
        
        return {"todos": todos}
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行任务列表更新"""
        todos_data = input_data["todos"]
        
        old_todos, new_todos = TodoManager.update_todos(todos_data)
        
        all_done = all(t.status == TodoStatus.COMPLETED for t in new_todos)
        
        # 保留completed状态的数据，不清空——前端需要展示"全部完成"的状态
        # if all_done and len(new_todos) > 0:
        #     TodoManager.clear_todos()
        
        return {
            "old_todos": [t.to_dict() for t in old_todos],
            "new_todos": [t.to_dict() for t in new_todos],
            "all_completed": all_done,
            "message": "Todos have been modified successfully. Ensure that you use the todo list to track your progress. Please mark items as completed as you work through them."
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取内容用于权限检查"""
        return None


class TodoReadTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    任务列表读取工具
    
    用于获取当前会话的任务列表
    """
    
    name = "todo_read"
    description = "Read the current todo list for the session"
    input_params = []
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        return {}
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """读取任务列表"""
        todos = TodoManager.get_todos()
        return {
            "todos": [t.to_dict() for t in todos],
            "count": len(todos),
            "pending": sum(1 for t in todos if t.status == TodoStatus.PENDING),
            "in_progress": sum(1 for t in todos if t.status == TodoStatus.IN_PROGRESS),
            "completed": sum(1 for t in todos if t.status == TodoStatus.COMPLETED)
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None
