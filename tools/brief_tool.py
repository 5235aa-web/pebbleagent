"""
Brief 消息工具

发送简短消息给用户，支持附件
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional, List
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class BriefTool(BaseTool[Dict[str, Any], str]):
    """
    Brief 消息工具
    
    发送消息给用户，支持：
    - Markdown 格式
    - 文件附件
    - 主动/被动状态标记
    """
    
    name = "brief"
    description = "发送简短消息给用户（支持 Markdown 和附件）"
    input_params = ["message", "attachments", "status"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "消息内容（支持 Markdown）"},
            "attachments": {"type": "array", "items": {"type": "string"}, "description": "附件文件路径列表"},
            "status": {"type": "string", "enum": ["normal", "proactive"], "description": "消息状态：normal=回复用户，proactive=主动通知"}
        },
        "required": ["message", "status"]
    }
    requires_permission = False
    danger_level = "low"
    parallel_safe = True
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        message = kwargs.get('message')
        status = kwargs.get('status')
        
        if not message:
            raise ValueError("message parameter is required")
        if status not in ['normal', 'proactive']:
            raise ValueError("status must be 'normal' or 'proactive'")
        
        return {
            'message': str(message),
            'attachments': kwargs.get('attachments', []),
            'status': status
        }
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """发送消息"""
        message = input_data['message']
        attachments = input_data.get('attachments', [])
        status = input_data['status']
        
        logger.info(f"[Brief] Sending {status} message: {message[:50]}...")
        
        # 处理附件
        resolved_attachments = []
        if attachments:
            for path in attachments:
                resolved = self._resolve_attachment(path)
                if resolved:
                    resolved_attachments.append(resolved)
        
        # 记录发送时间
        sent_at = datetime.now().isoformat()
        
        # 构建结果
        result = {
            'message': message,
            'attachments': resolved_attachments,
            'status': status,
            'sent_at': sent_at
        }
        
        n = len(resolved_attachments)
        suffix = f" ({n} attachment{'s' if n != 1 else ''} included)" if n > 0 else ""
        
        return f"Message delivered to user.{suffix}"
    
    def _resolve_attachment(self, path: str) -> Optional[Dict[str, Any]]:
        """解析附件路径"""
        if not path:
            return None
        
        # 解析相对路径
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        
        if not os.path.exists(path):
            logger.warning(f"[Brief] Attachment not found: {path}")
            return None
        
        # 获取文件信息
        try:
            stat = os.stat(path)
            is_image = self._is_image_file(path)
            
            return {
                'path': path,
                'size': stat.st_size,
                'is_image': is_image
            }
        except Exception as e:
            logger.warning(f"[Brief] Failed to resolve attachment: {e}")
            return None
    
    def _is_image_file(self, path: str) -> bool:
        """检查是否为图片文件"""
        image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'}
        return os.path.splitext(path)[1].lower() in image_extensions
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        """提取消息内容用于显示"""
        return input_data.get('message', '')[:200]
