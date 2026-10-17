"""Todo, notes, questions, plans, skills and tool discovery."""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Dict, List



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


    async def _tool_experience_write(self, statement: str, kind: str = "preference",
                                     rationale: str = "", scope: str = "project",
                                     keywords: list = None, context=None) -> str:
        """写入一条经验到经验账本（跨会话检索并注入系统提示词）"""
        try:
            from ..memory import get_ledger
            from ..memory.ledger import VALID_KINDS
            text = (statement or "").strip()
            if not text:
                return "Error: statement is required"
            if kind not in VALID_KINDS:
                return f"Error: kind must be one of {', '.join(VALID_KINDS)}"
            ledger = get_ledger()
            ledger.set_project(getattr(context, "project_path", "") or self._current_project_path)
            agent_type = getattr(context, "agent_name", "") if context else ""
            entry = ledger.create(
                kind=kind,
                statement=text,
                rationale=(rationale or "").strip(),
                applies_to={"keywords": [k for k in (keywords or []) if isinstance(k, str)]},
                confidence=0.8,
                scope=scope,
                agent_type=agent_type or None,
            )
            if not entry:
                return "Experience already recorded (duplicate or reinforced)"
            return f"Recorded experience {entry['id']} ({entry['kind']}, {entry['scope']})"
        except Exception as e:
            logger.error(f"[ToolExecutor] experience_write error: {e}")
            return f"Error: {str(e)}"


    async def _tool_experience_update(self, entry_id: str, statement: str = "", kind: str = "",
                                      rationale: str = "", confidence=None, context=None) -> str:
        """更新一条经验，返回修改前后对比；作用域不可修改。"""
        try:
            from ..memory import get_ledger
            from ..memory.ledger import VALID_KINDS
            if not entry_id:
                return "Error: id is required"
            ledger = get_ledger()
            ledger.set_project(getattr(context, "project_path", "") or self._current_project_path)
            before = ledger.get(entry_id)
            if not before:
                return f"Error: experience {entry_id} not found"
            if before.get("scope") == "project" and before.get("project_id") != ledger.project_id:
                return "Error: entry belongs to another project"
            updates = {}
            if (statement or "").strip():
                text = statement.strip()
                if len(text) > 120:
                    return "Error: statement must be at most 120 characters"
                updates["statement"] = text
            if kind:
                if kind not in VALID_KINDS:
                    return f"Error: kind must be one of {', '.join(VALID_KINDS)}"
                updates["kind"] = kind
            if (rationale or "").strip():
                text = rationale.strip()
                if len(text) > 200:
                    return "Error: rationale must be at most 200 characters"
                updates["rationale"] = text
            if confidence is not None:
                value = float(confidence)
                if value < 0.0 or value > 1.0:
                    return "Error: confidence must be between 0 and 1"
                updates["confidence"] = value
            if not updates:
                return "Error: no fields to update"
            after = ledger.update(entry_id, **updates)
            if not after:
                return f"Error: experience {entry_id} not found"
            changes = []
            for key in ("statement", "kind", "rationale", "confidence"):
                if key in updates:
                    changes.append(f"{key}: {before.get(key)!r} -> {after.get(key)!r}")
            return "Updated experience " + entry_id + "\n" + "\n".join(changes)
        except Exception as e:
            logger.error(f"[ToolExecutor] experience_update error: {e}")
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
            from tools.plan_mode_tool import PlanModeManager
            
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
