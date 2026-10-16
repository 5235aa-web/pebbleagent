"""
ToolSearch工具 - 工具搜索

提供工具搜索和发现功能
"""
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Dict, Any, Optional, List
import logging
import re

logger = logging.getLogger(__name__)


class ToolSearchTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    工具搜索工具
    
    搜索可用的工具
    """
    
    name = "tool_search"
    description = """Query to find deferred tools. Use "select:<tool_name>" for direct selection, or keywords to search.

This tool helps you discover and select from available tools:
- Use keywords to search for tools by name or description
- Use "select:<tool_name>" to directly select a specific tool
- Returns matching tools with their descriptions"""
    
    input_params = ["query", "max_results"]
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        query = kwargs.get("query")
        if not query:
            raise ValueError("query parameter is required")
        
        return {
            "query": str(query),
            "max_results": kwargs.get("max_results", 5)
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具搜索"""
        query = input_data["query"]
        max_results = input_data.get("max_results", 5)
        
        try:
            from .tool_registry import ToolRegistry
            
            all_tools = list(ToolRegistry._tools.values())
            
            if query.startswith("select:"):
                tool_name = query[7:].strip()
                for tool in all_tools:
                    if tool.name.lower() == tool_name.lower():
                        return {
                            "matches": [tool.name],
                            "query": query,
                            "total_deferred_tools": len(all_tools),
                            "selected_tool": {
                                "name": tool.name,
                                "description": tool.description[:500] if tool.description else ""
                            },
                            "message": f"Selected tool: {tool.name}"
                        }
                
                return {
                    "matches": [],
                    "query": query,
                    "total_deferred_tools": len(all_tools),
                    "error": f"Tool not found: {tool_name}",
                    "message": f"Tool '{tool_name}' not found"
                }
            
            query_lower = query.lower()
            query_words = re.findall(r'\w+', query_lower)
            
            scored_tools = []
            for tool in all_tools:
                score = 0
                name_lower = tool.name.lower()
                desc_lower = (tool.description or "").lower()
                
                if query_lower == name_lower:
                    score += 100
                
                if query_lower in name_lower:
                    score += 50
                
                for word in query_words:
                    if word in name_lower:
                        score += 20
                    if word in desc_lower:
                        score += 5
                
                if score > 0:
                    scored_tools.append((tool, score))
            
            scored_tools.sort(key=lambda x: x[1], reverse=True)
            
            matches = [tool.name for tool, score in scored_tools[:max_results]]
            
            return {
                "matches": matches,
                "query": query,
                "total_deferred_tools": len(all_tools),
                "message": f"Found {len(matches)} matching tools"
            }
            
        except ImportError:
            return {
                "matches": [],
                "query": query,
                "total_deferred_tools": 0,
                "error": "ToolRegistry not available",
                "message": "Tool registry not available"
            }
        except Exception as e:
            logger.error(f"ToolSearch error: {e}")
            return {
                "matches": [],
                "query": query,
                "total_deferred_tools": 0,
                "error": str(e),
                "message": f"Search failed: {str(e)}"
            }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None


class ListToolsTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    列出所有工具
    
    列出所有可用的工具
    """
    
    name = "list_tools"
    description = "List all available tools with their descriptions"
    input_params = []
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        return {}
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """列出所有工具"""
        try:
            from .tool_registry import ToolRegistry
            
            all_tools = list(ToolRegistry._tools.values())
            
            tools_info = []
            for tool in all_tools:
                tools_info.append({
                    "name": tool.name,
                    "description": (tool.description or "")[:200],
                    "requires_permission": getattr(tool, "requires_permission", True),
                    "danger_level": getattr(tool, "danger_level", "medium")
                })
            
            tools_info.sort(key=lambda x: x["name"])
            
            return {
                "tools": tools_info,
                "count": len(tools_info),
                "message": f"Found {len(tools_info)} available tools"
            }
            
        except ImportError:
            return {
                "tools": [],
                "count": 0,
                "error": "ToolRegistry not available",
                "message": "Tool registry not available"
            }
        except Exception as e:
            logger.error(f"ListTools error: {e}")
            return {
                "tools": [],
                "count": 0,
                "error": str(e),
                "message": f"Failed to list tools: {str(e)}"
            }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None
