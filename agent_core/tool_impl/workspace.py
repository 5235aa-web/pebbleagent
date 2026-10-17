"""Todo, notes, questions, plans, skills and tool discovery."""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..truncation_manager import truncate_output
from ..code_network_guardV2 import (
    scan_code_for_scraping_behavior,
    get_rate_limiter,
    get_path_depth_guard,
    get_download_guard,
    reset_all_guards,
)
from ..code_destructive_guard import scan_code_for_destructive_behavior

logger = logging.getLogger(__name__)



class WorkspaceToolsMixin:

    async def _tool_todo_write(self, todos: list) -> str:
        """更新任务列表"""
        if not todos:
            return "Error: todos 参数为空"
        
        try:
            from tools.todo_tool import TodoManager
            
            processed_todos = []
            for i, item in enumerate(todos):
                if not isinstance(item, dict):
                    return f"Error: todo item {i} 必须是字典"
                if "content" not in item:
                    return f"Error: todo item {i} 必须有 'content' 字段"
                
                if "id" not in item:
                    item["id"] = f"todo_{uuid.uuid4().hex[:8]}"
                
                status = item.get("status", "pending")
                if status not in ["pending", "in_progress", "completed"]:
                    return f"Error: todo item {i} 状态无效: {status}"
                
                processed_todos.append(item)
            
            old_todos, new_todos = TodoManager.update_todos(processed_todos)
            
            return json.dumps({
                "old_count": len(old_todos),
                "new_count": len(new_todos),
                "todos": [t.to_dict() for t in new_todos]
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: TodoManager not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_todo_read(self) -> str:
        """读取任务列表"""
        try:
            from tools.todo_tool import TodoManager, TodoStatus
            
            todos = TodoManager.get_todos()
            
            return json.dumps({
                "todos": [t.to_dict() for t in todos],
                "count": len(todos),
                "pending": sum(1 for t in todos if t.status == TodoStatus.PENDING),
                "in_progress": sum(1 for t in todos if t.status == TodoStatus.IN_PROGRESS),
                "completed": sum(1 for t in todos if t.status == TodoStatus.COMPLETED)
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: TodoManager not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_clarify(self, questions: List[Dict[str, Any]], question_id: str = None) -> str:
        """向用户提出澄清问题"""
        if not questions:
            return "Error: No questions provided"
        
        if len(questions) < 1 or len(questions) > 4:
            return "Error: Questions must have 1-4 items"
        
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                return f"Error: Question {i} must be a dictionary"
            if "prompt" not in q:
                return f"Error: Question {i} must have 'prompt' field"
            if "options" not in q:
                return f"Error: Question {i} must have 'options' field"
        
        try:
            from tools.ask_question_tool import QuestionManager
            
            # 注意：set_pending_questions 已由 agent_chat_service_v2.py 在 yield ask_question
            # 事件之前执行，此处不再重复调用，避免清掉用户已提交的答案。
            
            # 等待用户回答（阻塞直到前端提交答案）
            answers = await asyncio.wait_for(
                QuestionManager.wait_for_answers(),
                timeout=300.0
            )
            
            if answers is None:
                return json.dumps({
                    "cancelled": True,
                    "message": "Question was cancelled"
                }, ensure_ascii=False)
            
            return json.dumps({
                "questions": questions,
                "answers": answers.get("answers", {}),
                "cancelled": False
            }, ensure_ascii=False, indent=2)
        
        except asyncio.TimeoutError:
            logger.warning("[ToolExecutor] clarify timed out")
            return json.dumps({
                "cancelled": True,
                "message": "Question timed out"
            }, ensure_ascii=False)
        except ImportError:
            return "Error: QuestionManager not available"
        except Exception as e:
            logger.error(f"[ToolExecutor] clarify error: {e}")
            return f"Error: {str(e)}"


    async def _tool_usernote_write(self, content: str) -> str:
        """写入用户便签"""
        try:
            import os as _uos
            _data_home = _uos.environ.get("PEBBLE_HOME") or _uos.path.join(_uos.path.expanduser("~"), ".pebble")
            _uos.makedirs(_data_home, exist_ok=True)
            _un_path = _uos.path.join(_data_home, "usernote.json")
            with open(_un_path, "w", encoding="utf-8") as _f:
                from datetime import datetime
                json.dump({"content": content, "updated_at": datetime.now().isoformat()}, _f, ensure_ascii=False)
            return f"usernote 已更新 ({len(content)} 字符)"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_list_skills(self) -> str:
        """列出所有可用的技能"""
        try:
            from skills_loader import list_skills
            skills = list_skills()
            
            if not skills:
                return "当前没有可用的技能。"
            
            result = "可用技能列表：\n\n"
            for skill in skills:
                result += f"- **{skill['name']}**: {skill['description']}\n"
            
            result += "\n使用 load_skill(name) 加载特定技能的完整内容。"
            return result
        except ImportError:
            return "Error: skills_loader not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_load_skill(self, name: str) -> str:
        """加载指定技能的完整内容"""
        if not name:
            return "Error: 请指定技能名称"
        
        try:
            from skills_loader import load_skill
            content = load_skill(name)
            
            if content:
                return f"=== 技能: {name} ===\n\n{content}"
            else:
                return f"未找到技能 '{name}'。使用 list_skills() 查看可用技能。"
        except ImportError:
            return "Error: skills_loader not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_enter_plan_mode(self) -> str:
        """进入计划模式"""
        try:
            from tools.plan_mode_tool import PlanModeManager, PlanMode
            
            result = PlanModeManager.enter_plan_mode()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_exit_plan_mode(self, plan_title: str = None, 
                                    plan_description: str = None,
                                    steps: List[Dict] = None) -> str:
        """退出计划模式"""
        try:
            from tools.plan_mode_tool import PlanModeManager
            
            plan_data = None
            if plan_title and plan_description:
                PlanModeManager.create_plan(plan_title, plan_description, steps or [])
                plan_data = {
                    "title": plan_title,
                    "description": plan_description,
                    "steps": steps or []
                }
            
            result = PlanModeManager.exit_plan_mode(plan_data)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_create_plan(self, title: str, description: str = "", 
                                 steps: List[Dict] = None) -> str:
        """创建计划"""
        try:
            from tools.plan_mode_tool import PlanModeManager, PlanMode
            
            if PlanModeManager.get_mode() != PlanMode.PLAN:
                return json.dumps({
                    "success": False,
                    "error": "Not in plan mode. Use enter_plan_mode first."
                }, ensure_ascii=False, indent=2)
            
            if not title:
                return "Error: title is required"
            
            plan = PlanModeManager.create_plan(title, description, steps or [])
            
            return json.dumps({
                "success": True,
                "plan": plan.to_dict(),
                "message": f"Created plan: {plan.title}"
            }, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_get_plan(self) -> str:
        """获取当前计划"""
        try:
            from tools.plan_mode_tool import PlanModeManager
            
            plan = PlanModeManager.get_current_plan()
            
            if not plan:
                return json.dumps({
                    "success": True,
                    "plan": None,
                    "message": "No current plan"
                }, ensure_ascii=False, indent=2)
            
            return json.dumps({
                "success": True,
                "plan": plan.to_dict(),
                "mode": PlanModeManager.get_mode().value,
                "message": f"Current plan: {plan.title}"
            }, ensure_ascii=False, indent=2)
        except ImportError:
            return "Error: plan_mode_tool not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_tool_search(self, query: str, max_results: int = 5) -> str:
        """搜索可用工具"""
        if not query:
            return "Error: No query provided"
        
        import re
        
        try:
            from tools.tool_registry import ToolRegistry
            
            all_tools = list(ToolRegistry._tools.values())
            
            if query.startswith("select:"):
                tool_name = query[7:].strip()
                for tool in all_tools:
                    if tool.name.lower() == tool_name.lower():
                        return json.dumps({
                            "matches": [tool.name],
                            "selected_tool": {
                                "name": tool.name,
                                "description": tool.description[:500] if tool.description else ""
                            }
                        }, ensure_ascii=False, indent=2)
                
                return json.dumps({
                    "matches": [],
                    "error": f"Tool not found: {tool_name}"
                }, ensure_ascii=False, indent=2)
            
            # 关键词搜索
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
            
            return json.dumps({
                "matches": matches,
                "query": query,
                "total_deferred_tools": len(all_tools)
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: ToolRegistry not available"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_list_tools(self) -> str:
        """列出所有可用工具"""
        try:
            from tools.tool_registry import ToolRegistry
            
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
            
            return json.dumps({
                "tools": tools_info,
                "count": len(tools_info)
            }, ensure_ascii=False, indent=2)
        
        except ImportError:
            return "Error: ToolRegistry not available"
        except Exception as e:
            return f"Error: {str(e)}"
