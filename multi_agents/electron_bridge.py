"""
Electron HTTP Bridge Client

Python 端通过此模块与 Electron 主进程的 BridgeServer 通信。
不需要经过 Vue 前端，直接通过 localhost HTTP 控制内置浏览器。

端口发现：Electron 在启动时将端口写入根目录的 .bridge_port 文件。
"""

import os
import json
import logging
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Electron 桥接端口文件
BRIDGE_PORT_FILE = PROJECT_ROOT / ".bridge_port"

# 默认超时
DEFAULT_TIMEOUT = 30.0


class ElectronBridgeError(Exception):
    """Electron 桥接通信错误"""
    pass


class ElectronBridge:
    """
    Electron HTTP 桥接客户端
    
    通过 localhost HTTP 直接调用 Electron 主进程的浏览器能力。
    自动发现端口，懒加载连接。
    """
    
    def __init__(self):
        self._base_url: Optional[str] = None
        self._last_check: float = 0
        self._check_interval: float = 5.0  # 每 5 秒重新检查端口文件
    
    @property
    def base_url(self) -> Optional[str]:
        """获取桥接服务器地址，自动从端口文件发现"""
        now = time.time()
        if self._base_url is None or (now - self._last_check) > self._check_interval:
            self._last_check = now
            port = self._read_port_file()
            if port:
                self._base_url = f"http://127.0.0.1:{port}"
            else:
                self._base_url = None
        return self._base_url
    
    def _read_port_file(self) -> Optional[int]:
        """从 .bridge_port 文件读取端口号"""
        try:
            if BRIDGE_PORT_FILE.exists():
                content = BRIDGE_PORT_FILE.read_text().strip()
                port = int(content)
                if 0 < port < 65536:
                    return port
                logger.warning(f"[ElectronBridge] Invalid port in bridge file: {port}")
            return None
        except (ValueError, OSError) as e:
            logger.warning(f"[ElectronBridge] Failed to read port file: {e}")
            return None
    
    async def _request(self, method: str, path: str, body: Optional[Dict] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """发送 HTTP 请求到桥接服务器"""
        base = self.base_url
        if not base:
            raise ElectronBridgeError(
                "Electron bridge is not available. "
                "Make sure the Electron app is running with the browser feature enabled."
            )
        
        url = f"{base}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                if method == "GET":
                    resp = await client.get(url)
                else:
                    resp = await client.post(url, json=body or {})
                
                if resp.status_code == 404:
                    raise ElectronBridgeError(f"Bridge endpoint not found: {path}")
                if resp.status_code >= 500:
                    raise ElectronBridgeError(f"Bridge server error ({resp.status_code}): {resp.text}")
                
                data = resp.json()
                if not data.get("success"):
                    raise ElectronBridgeError(data.get("error", "Unknown bridge error"))
                return data
                
            except httpx.RequestError as e:
                raise ElectronBridgeError(f"Cannot connect to Electron bridge at {base}: {e}")
    
    def is_port_file_valid(self) -> bool:
        """检查端口文件是否存在（快速检查，不发起 HTTP 请求）"""
        try:
            if BRIDGE_PORT_FILE.exists():
                content = BRIDGE_PORT_FILE.read_text().strip()
                port = int(content)
                return 0 < port < 65536
            return False
        except (ValueError, OSError):
            return False
    
    async def is_available_async(self) -> bool:
        """异步检查桥接服务是否可用"""
        try:
            result = await self._request("GET", "/health", timeout=5.0)
            return result.get("status") == "ok"
        except Exception:
            return False
    
    async def health(self) -> Dict[str, Any]:
        """健康检查"""
        return await self._request("GET", "/health")
    
    # ==================== Tab Operations ====================
    
    async def create_tab(self, url: str = "about:blank") -> Dict[str, Any]:
        """创建新标签页"""
        return await self._request("POST", "/browser/create-tab", {"url": url})
    
    async def close_tab(self, tab_id: str):
        """关闭标签页"""
        await self._request("POST", "/browser/close-tab", {"tabId": tab_id})
    
    # ==================== Navigation ====================
    
    async def navigate(self, tab_id: str, url: str, timeout: int = 30) -> Dict[str, Any]:
        """导航到 URL 并等待页面加载完成"""
        return await self._request("POST", "/browser/navigate", {
            "tabId": tab_id,
            "url": url,
            "timeout": timeout
        }, timeout=timeout + 5)
    
    # ==================== Content ====================
    
    async def get_page_content(self, tab_id: str) -> Optional[Dict[str, Any]]:
        """获取页面内容（text, html, links, images, metadata）"""
        data = await self._request("POST", "/browser/get-content", {"tabId": tab_id})
        return data.get("content")
    
    async def execute_script(self, tab_id: str, script: str) -> Any:
        """在页面中执行 JavaScript"""
        data = await self._request("POST", "/browser/execute-script", {
            "tabId": tab_id,
            "script": script
        })
        return data.get("result")
    
    # ==================== Actions ====================
    
    async def click(self, tab_id: str, selector: str) -> bool:
        """点击页面元素"""
        data = await self._request("POST", "/browser/click", {
            "tabId": tab_id,
            "selector": selector
        })
        return data.get("success", False)
    
    async def input_text(self, tab_id: str, selector: str, text: str) -> bool:
        """在输入框中输入文本"""
        data = await self._request("POST", "/browser/input", {
            "tabId": tab_id,
            "selector": selector,
            "text": text
        })
        return data.get("success", False)
    
    # ==================== Search ====================
    
    async def search(self, query: str, engine: str = "bing", timeout: int = 15) -> Dict[str, Any]:
        """
        使用搜索引擎搜索，返回页面内容
        
        这是一个快捷操作：创建临时标签 → 导航到搜索 → 获取内容 → 关闭标签
        
        Args:
            query: 搜索关键词
            engine: 搜索引擎 (bing/google/baidu)
            timeout: 超时秒数
            
        Returns:
            {"success": bool, "query": str, "content": {...}}
        """
        return await self._request("POST", "/browser/search", {
            "query": query,
            "engine": engine,
            "timeout": timeout
        }, timeout=timeout + 5)
    
    # ==================== Download ====================
    
    async def trigger_download(self, url: str, filename: Optional[str] = None, save_path: Optional[str] = None) -> Dict[str, Any]:
        """触发文件下载
        
        Args:
            url: 下载 URL
            filename: 建议的文件名（可选）
            save_path: 保存路径（传此参数则跳过保存对话框，直接保存到指定路径）
        """
        body = {"url": url}
        if filename:
            body["filename"] = filename
        if save_path:
            body["savePath"] = save_path
        return await self._request("POST", "/browser/download", body)
    
    async def get_downloads(self) -> List[Dict[str, Any]]:
        """获取下载列表"""
        data = await self._request("POST", "/browser/get-downloads")
        return data.get("downloads", [])


# ==================== 全局单例 ====================

_bridge_instance: Optional[ElectronBridge] = None


def get_bridge() -> ElectronBridge:
    """获取 ElectronBridge 全局单例"""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = ElectronBridge()
    return _bridge_instance


def is_bridge_available() -> bool:
    """检查桥接是否可用（仅检查端口文件，不发起 HTTP，可安全在 async 上下文中调用）"""
    try:
        b = get_bridge()
        return b.is_port_file_valid()
    except Exception:
        return False
