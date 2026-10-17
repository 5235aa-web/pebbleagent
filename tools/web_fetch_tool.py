"""
WebSearch工具 - 前端中介浏览器搜索模式

不再使用后端 HTTP 抓取，而是通过前端内置浏览器执行搜索。
流程：brain.py 检测到 web_search → yield browser_tool_request SSE 事件
→ 前端 ChatView 用 browserApi 执行搜索 → POST 结果到 /chat/browser-tool-result
→ brain.py 恢复 LLM 生成

关键约束：用户主动触发 + 单次使用，禁止一切遍历/批量行为。
"""
from .base_tool import BaseTool
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# 模块级存储：挂起等待浏览器结果的 asyncio.Event 和结果容器
# key 由 brain.py 生成（conversation_id + timestamp），用于匹配请求与响应
_pending_browser_searches: Dict[str, Dict[str, Any]] = {}


def register_browser_search_request(request_id: str, query: str) -> None:
    """注册一个待处理的浏览器搜索请求（由 brain.py 调用）"""
    _pending_browser_searches[request_id] = {
        "query": query,
        "event": None,  # asyncio.Event，由 brain.py 设置
        "result": None,  # 搜索结果，由 api_server.py 回调填充
        "error": None,
    }


def set_browser_search_event(request_id: str, event) -> None:
    """设置 asyncio.Event（由 brain.py 在创建 Event 后调用）"""
    if request_id in _pending_browser_searches:
        _pending_browser_searches[request_id]["event"] = event


def submit_browser_search_result(request_id: str, result: Dict[str, Any]) -> bool:
    """提交浏览器搜索结果并唤醒等待的协程（由 api_server.py 调用）
    
    返回 True 表示成功唤醒，False 表示请求不存在
    """
    if request_id not in _pending_browser_searches:
        return False
    
    entry = _pending_browser_searches[request_id]
    entry["result"] = result
    
    # 唤醒等待的协程
    event = entry.get("event")
    if event:
        event.set()
    
    return True


def get_and_cleanup_browser_search(request_id: str) -> Optional[Dict[str, Any]]:
    """获取并清理浏览器搜索结果（由 brain.py 在恢复后调用）"""
    entry = _pending_browser_searches.pop(request_id, None)
    if entry is None:
        return None
    return {
        "result": entry.get("result"),
        "error": entry.get("error"),
    }


class WebSearchTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    网页搜索工具（前端中介模式）
    
    不再直接发起 HTTP 请求抓取搜索结果。
    改为返回一个标记，由 brain.py 识别并发送 browser_tool_request 
    SSE 事件给前端，前端使用内置浏览器执行搜索后回传结果。
    
    严格约束：用户主动触发 + 单次使用，禁止遍历/批量行为。
    """
    
    name = "web_search"
    description = """Search the internet using the user's built-in browser. This is the ONLY way to search the web — do NOT use code (Python/Shell/etc.) to make HTTP requests or crawl pages.

Use this tool when:
- Real-time information is needed (weather, news, stock prices, etc.)
- You are unsure about factual knowledge
- User asks you to search or look up something online
- Latest data is required

IMPORTANT constraints:
- SINGLE search per conversation turn — no traversal, no batch, no automated multi-page access
- Results are returned automatically — you do NOT need to implement any search logic yourself
- DO NOT attempt to use code execution to simulate browser requests or web scraping

Returns structured search results with title, snippet, and URL for each result."""
    
    input_params = ["query"]
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query string"}
        },
        "required": ["query"]
    }
    requires_permission = True
    danger_level = "low"
    parallel_safe = False  # 浏览器中介模式需要 asyncio.Event 等待，不可并行
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        query = kwargs.get("query")
        if not query:
            raise ValueError("query parameter is required")
        
        return {
            "query": str(query),
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行网页搜索 - 前端中介模式
        
        此方法不会被 brain.py 直接调用执行搜索。
        brain.py 会检测 web_search 工具调用，改为发送 browser_tool_request 
        SSE 事件给前端，由前端浏览器执行搜索并回传结果。
        
        此方法仅作为 fallback（如果 ToolRegistry 直接执行了此工具），
        返回提示信息告知应使用前端中介模式。
        """
        query = input_data["query"]
        logger.warning(f"[WebSearchTool] Direct call detected (should use browser-mediated mode). Query: {query}")
        return {
            "query": query,
            "results": [],
            "message": "Web search should be executed via browser-mediated mode. This tool should not be called directly.",
            "_use_browser_mediation": True,  # 标记：brain.py 应改用浏览器中介模式
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return input_data.get("query")
