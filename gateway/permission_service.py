"""
权限申请服务
参考主流 Agent 产品的安全机制
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Callable, Awaitable
from dataclasses import dataclass, field
import uuid
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)

class PermissionType(str, Enum):
    """权限类型"""
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"
    FILE_DELETE = "file:delete"
    EXEC_COMMAND = "exec:command"
    NETWORK_FETCH = "network:fetch"
    APP_LAUNCH = "app:launch"
    CLIPBOARD_READ = "clipboard:read"
    CLIPBOARD_WRITE = "clipboard:write"
    SCREENSHOT = "screenshot"
    MCP_TOOL = "mcp:tool"          # MCP 服务器工具调用
    UNKNOWN_TOOL = "tool:unknown"  # 未映射的未知工具

class PermissionLevel(str, Enum):
    """危险等级"""
    LOW = "low"      # 🟢 低
    MEDIUM = "medium"  # 🟡 中
    HIGH = "high"    # 🟠 高
    CRITICAL = "critical"  # 🔴 极高

# 权限危险等级映射
PERMISSION_LEVELS: Dict[PermissionType, PermissionLevel] = {
    PermissionType.FILE_READ: PermissionLevel.MEDIUM,
    PermissionType.FILE_WRITE: PermissionLevel.HIGH,
    PermissionType.FILE_DELETE: PermissionLevel.CRITICAL,
    PermissionType.EXEC_COMMAND: PermissionLevel.CRITICAL,
    PermissionType.NETWORK_FETCH: PermissionLevel.LOW,
    PermissionType.APP_LAUNCH: PermissionLevel.HIGH,
    PermissionType.CLIPBOARD_READ: PermissionLevel.MEDIUM,
    PermissionType.CLIPBOARD_WRITE: PermissionLevel.LOW,
    PermissionType.SCREENSHOT: PermissionLevel.HIGH,
    PermissionType.MCP_TOOL: PermissionLevel.MEDIUM,
    PermissionType.UNKNOWN_TOOL: PermissionLevel.MEDIUM,
}

@dataclass
class PermissionRequest:
    """权限请求"""
    request_id: str
    permission_type: PermissionType
    target: str  # 具体路径/命令/URL
    reason: str  # AI 解释为什么需要
    timestamp: datetime = field(default_factory=datetime.utcnow)
    session_id: str = ""  # 用于记住选择

@dataclass
class PermissionDecision:
    """权限决策"""
    request_id: str
    approved: bool
    remember_minutes: int = 0  # 0 = 不记住

class PermissionService:
    """权限检查服务"""
    
    def __init__(self):
        # 待处理的权限请求
        self._pending_requests: Dict[str, PermissionRequest] = {}
        # 锁
        self._lock = asyncio.Lock()
        # 信任的权限会话：键为 (session_id, permission_type.value)，按"会话+权限类型"粒度记忆
        self._trusted_sessions: Dict[tuple, datetime] = {}
        # WebSocket 连接集合
        self._ws_connections: Set[Any] = set()
        # 等待决策的回调
        self._decision_futures: Dict[str, asyncio.Future] = {}
        # 推送回调（替代原生 WebSocket 广播，用于 ws_server 集成）
        self._push_callbacks: List[Callable[[dict], Awaitable[None]]] = []
    
    def register_push_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """注册推送回调，当有权限请求时调用"""
        self._push_callbacks.append(callback)
    
    def unregister_push_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """取消注册推送回调"""
        if callback in self._push_callbacks:
            self._push_callbacks.remove(callback)
    
    def add_ws_connection(self, ws: Any):
        """添加 WebSocket 连接"""
        self._ws_connections.add(ws)
        logger.info(f"WebSocket connection added. Total: {len(self._ws_connections)}")

    def remove_ws_connection(self, ws: Any):
        """移除 WebSocket 连接"""
        self._ws_connections.discard(ws)
        logger.info(f"WebSocket connection removed. Total: {len(self._ws_connections)}")
    
    async def broadcast_permission_request(self, request: PermissionRequest):
        """广播权限请求到所有 WebSocket 客户端和注册的回调"""
        message = {
            "type": "permission_request",
            "request_id": request.request_id,
            "permission_type": request.permission_type.value,
            "target": request.target,
            "reason": request.reason,
            "timestamp": request.timestamp.isoformat(),
            "session_id": request.session_id,
            "level": PERMISSION_LEVELS.get(request.permission_type, PermissionLevel.MEDIUM).value,
        }
                
        # 调用注册的回调（ws_server 集成）
        for i, cb in enumerate(self._push_callbacks):
            try:
                await cb(message)
            except Exception as e:
                logger.warning(f"[PermissionService] Push callback failed: {e}")
                        
        # 广播到原始 WebSocket 连接（兼容旧方式）
        disconnected = set()
        for ws in self._ws_connections:
            try:
                await ws.send_text(json.dumps(message))
            except Exception as e:
                disconnected.add(ws)

        # 清理断开的连接
        for ws in disconnected:
            self.remove_ws_connection(ws)
    
    def get_permission_level(self, permission_type: PermissionType) -> PermissionLevel:
        """获取权限危险等级"""
        return PERMISSION_LEVELS.get(permission_type, PermissionLevel.MEDIUM)
    
    def format_permission_type(self, permission_type: PermissionType) -> str:
        """格式化权限类型显示"""
        labels = {
            PermissionType.FILE_READ: "📁 读取文件",
            PermissionType.FILE_WRITE: "✏️ 写入文件",
            PermissionType.FILE_DELETE: "🗑️ 删除文件",
            PermissionType.EXEC_COMMAND: "💻 执行命令",
            PermissionType.NETWORK_FETCH: "🌐 访问网络",
            PermissionType.APP_LAUNCH: "🚀 启动应用",
            PermissionType.CLIPBOARD_READ: "📋 读取剪贴板",
            PermissionType.CLIPBOARD_WRITE: "📝 写入剪贴板",
            PermissionType.SCREENSHOT: "📸 截取屏幕",
            PermissionType.MCP_TOOL: "🔌 MCP 工具调用",
            PermissionType.UNKNOWN_TOOL: "❓ 未知工具",
        }
        return labels.get(permission_type, permission_type.value)
    
    async def check_permission(self, permission_type: PermissionType, session_id: str = "") -> bool:
        """检查"会话+权限类型"组合是否已信任"""
        if not session_id:
            return False
        key = (session_id, permission_type.value)
        expiration = self._trusted_sessions.get(key)
        if expiration and expiration > datetime.utcnow():
            return True
        # 过期或不存在
        if key in self._trusted_sessions:
            del self._trusted_sessions[key]
        return False
    
    async def request_permission(
        self,
        permission_type: PermissionType,
        target: str,
        reason: str,
        session_id: str = ""
    ) -> Optional[PermissionRequest]:
        """创建权限请求并广播"""
        async with self._lock:
            request_id = str(uuid.uuid4())
            request = PermissionRequest(
                request_id=request_id,
                permission_type=permission_type,
                target=target,
                reason=reason,
                session_id=session_id
            )
            self._pending_requests[request_id] = request
                    
        # 广播给前端
        await self.broadcast_permission_request(request)
                
        return request
    
    async def wait_for_decision(self, request_id: str, timeout: int = 120) -> Optional[PermissionDecision]:
        """等待用户决策，超时自动拒绝"""
        # 创建未来用于接收决策
        future = asyncio.get_event_loop().create_future()
        self._decision_futures[request_id] = future
        
        try:
            # 等待决策或超时
            decision = await asyncio.wait_for(future, timeout=timeout)
            return decision
        except asyncio.TimeoutError:
            logger.warning(f"Permission request {request_id} timed out")
            return PermissionDecision(request_id=request_id, approved=False, remember_minutes=0)
        finally:
            self._decision_futures.pop(request_id, None)
    
    async def process_decision(self, request_id: str, approved: bool, remember_minutes: int = 0):
        """处理用户决策"""
        async with self._lock:
            request = self._pending_requests.pop(request_id, None)
        
        if not request:
            logger.warning(f"Request {request_id} not found")
            return
        
        # 如果记住，按"会话+权限类型"加入信任列表（例如"记住读取"不会放行命令执行）
        if approved and remember_minutes > 0 and request.session_id:
            expiration = datetime.utcnow() + timedelta(minutes=remember_minutes)
            key = (request.session_id, request.permission_type.value)
            self._trusted_sessions[key] = expiration
            logger.info(f"Session {request.session_id} trusted for {request.permission_type.value} for {remember_minutes} minutes")
        
        # 通知等待者
        if request_id in self._decision_futures:
            decision = PermissionDecision(
                request_id=request_id,
                approved=approved,
                remember_minutes=remember_minutes
            )
            self._decision_futures[request_id].set_result(decision)
    
    def get_pending_requests(self) -> List[PermissionRequest]:
        """获取所有待处理请求"""
        return list(self._pending_requests.values())
    
    def clear_expired_sessions(self):
        """清理过期的信任会话"""
        now = datetime.utcnow()
        expired = [key for key, exp in self._trusted_sessions.items() if exp <= now]
        for key in expired:
            del self._trusted_sessions[key]

# 全局单例
_permission_service: Optional[PermissionService] = None

def get_permission_service() -> PermissionService:
    """获取权限服务单例"""
    global _permission_service
    if _permission_service is None:
        _permission_service = PermissionService()
    return _permission_service
