"""
PlanMode工具 - 计划模式管理

提供计划模式（进入/退出/创建/查看计划）
"""
from .base_tool import BaseTool
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import logging
import time

logger = logging.getLogger(__name__)


class PlanMode(str, Enum):
    """计划模式状态"""
    NORMAL = "normal"
    PLAN = "plan"


@dataclass
class PlanStep:
    """计划步骤"""
    id: str
    description: str
    status: str = "pending"
    details: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "details": self.details
        }


@dataclass
class Plan:
    """计划"""
    id: str
    title: str
    description: str
    steps: List[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = "draft"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "status": self.status
        }


class PlanModeManager:
    """计划模式管理器（单例模式）"""
    
    _instance: Optional["PlanModeManager"] = None
    _mode: PlanMode = PlanMode.NORMAL
    _current_plan: Optional[Plan] = None
    _plans: Dict[str, Plan] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_mode(cls) -> PlanMode:
        """获取当前模式"""
        return cls._mode
    
    @classmethod
    def enter_plan_mode(cls) -> Dict[str, Any]:
        """进入计划模式"""
        if cls._mode == PlanMode.PLAN:
            return {
                "success": True,
                "message": "当前已在计划模式",
                "mode": cls._mode.value
            }
        
        cls._mode = PlanMode.PLAN
        logger.info("Entered plan mode")
        
        return {
            "success": True,
            "message": "已进入计划模式。先梳理代码与方案，再开始改动。",
            "mode": cls._mode.value
        }
    
    @classmethod
    def exit_plan_mode(cls, plan_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """退出计划模式"""
        if cls._mode == PlanMode.NORMAL:
            return {
                "success": True,
                "message": "当前不在计划模式",
                "mode": cls._mode.value
            }
        
        cls._mode = PlanMode.NORMAL
        
        if plan_data and cls._current_plan:
            cls._current_plan.status = "approved"
            cls._plans[cls._current_plan.id] = cls._current_plan
        
        logger.info("Exited plan mode")
        
        return {
            "success": True,
            "message": "已退出计划模式，可以开始实施。",
            "mode": cls._mode.value,
            "plan": cls._current_plan.to_dict() if cls._current_plan else None
        }
    
    @classmethod
    def create_plan(cls, title: str, description: str, steps: List[Dict[str, Any]]) -> Plan:
        """创建新计划"""
        import uuid
        plan_id = str(uuid.uuid4())[:8]
        
        plan_steps = []
        for i, step_data in enumerate(steps):
            step = PlanStep(
                id=f"{plan_id}-{i+1}",
                description=step_data.get("description", ""),
                status=step_data.get("status", "pending"),
                details=step_data.get("details")
            )
            plan_steps.append(step)
        
        plan = Plan(
            id=plan_id,
            title=title,
            description=description,
            steps=plan_steps
        )
        
        cls._current_plan = plan
        return plan
    
    @classmethod
    def get_current_plan(cls) -> Optional[Plan]:
        """获取当前计划"""
        return cls._current_plan
    
    @classmethod
    def update_plan_step(cls, step_id: str, status: str, details: Optional[str] = None) -> bool:
        """更新计划步骤"""
        if not cls._current_plan:
            return False
        
        for step in cls._current_plan.steps:
            if step.id == step_id:
                step.status = status
                if details:
                    step.details = details
                return True
        
        return False
    
    @classmethod
    def clear_plan(cls) -> None:
        """清除当前计划"""
        cls._current_plan = None


class PlanModeEnterTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    进入计划模式工具
    
    用于复杂任务的规划和设计
    """
    
    name = "enter_plan_mode"
    description = """Switch to plan mode to design an approach before coding.

When to use:
- Complex multi-step tasks requiring exploration and design
- Tasks where you need to understand the codebase before making changes
- When the user wants to see a plan before implementation

In plan mode:
- Focus on exploring the codebase and understanding the structure
- Design an implementation approach
- Create a detailed plan with steps using create_plan
- Do not make any code changes until exiting plan mode

IMPORTANT: After creating your plan, you MUST call exit_plan_mode, then IMMEDIATELY start executing the plan.
The workflow is: enter_plan_mode -> create_plan -> exit_plan_mode -> EXECUTE each step!"""
    
    input_params = []
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        return {}
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """进入计划模式"""
        return PlanModeManager.enter_plan_mode()
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None


class PlanModeExitTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    退出计划模式工具
    
    完成规划后返回正常模式
    """
    
    name = "exit_plan_mode"
    description = """Exit plan mode and return to normal execution mode.

When to use:
- After completing your exploration and design
- When you have a clear implementation plan
- When ready to start coding

IMPORTANT: After exiting plan mode, you MUST immediately start executing the plan!
Do NOT stop after exiting plan mode. Begin implementing each step using write_file, execute_code, etc.

Provide:
- plan_title: Short title for your plan
- plan_description: Brief description
- steps: Array of step objects with "description" field"""
    
    input_params = ["plan_title", "plan_description", "steps"]
    requires_permission = False
    danger_level = "low"
    
    parameters_schema = {
        "type": "object",
        "properties": {
            "plan_title": {
                "type": "string",
                "description": "Short title for the plan"
            },
            "plan_description": {
                "type": "string",
                "description": "Brief description of the plan"
            },
            "steps": {
                "type": "array",
                "description": "Array of step objects for the plan",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Description of the step"
                        },
                        "details": {
                            "type": "string",
                            "description": "Additional details for the step"
                        }
                    },
                    "required": ["description"]
                }
            }
        }
    }
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        plan_title = kwargs.get("plan_title")
        plan_description = kwargs.get("plan_description")
        steps = kwargs.get("steps", [])
        
        if plan_title or plan_description or steps:
            if not plan_title:
                raise ValueError("plan_title is required when providing a plan")
            if not plan_description:
                raise ValueError("plan_description is required when providing a plan")
            if not isinstance(steps, list):
                raise ValueError("steps must be a list")
        
        return {
            "plan_title": plan_title,
            "plan_description": plan_description,
            "steps": steps
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """退出计划模式"""
        plan_title = input_data.get("plan_title")
        plan_description = input_data.get("plan_description")
        steps = input_data.get("steps", [])
        
        plan_data = None
        if plan_title and plan_description:
            PlanModeManager.create_plan(plan_title, plan_description, steps)
            plan_data = {
                "title": plan_title,
                "description": plan_description,
                "steps": steps
            }
        
        return PlanModeManager.exit_plan_mode(plan_data)
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None


class CreatePlanTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    创建计划工具
    
    在计划模式下创建详细计划
    """
    
    name = "create_plan"
    description = """Create a detailed implementation plan with structured steps.

Use this tool in plan mode to:
- Document your understanding of the task
- Break down the implementation into steps
- Identify potential challenges
- Plan your approach

IMPORTANT: Creating a plan is ONLY the planning phase. After creating a plan, you MUST:
1. Call exit_plan_mode to exit planning mode
2. Then IMMEDIATELY start executing the plan using write_file, execute_code, etc.
Do NOT stop after creating a plan - you must execute it!

Parameters:
- title: Short title for the plan
- description: Detailed description of what the plan accomplishes
- steps: Array of step objects, each with "description" field, e.g. [{"description": "Create HTML file"}, {"description": "Write CSS styles"}]"""
    
    input_params = ["title", "description", "steps", "plan"]
    requires_permission = False
    danger_level = "low"
    
    parameters_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the plan"
            },
            "description": {
                "type": "string",
                "description": "Detailed description of what the plan accomplishes"
            },
            "steps": {
                "type": "array",
                "description": "Array of step objects, each with a description field",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Description of the step"
                        },
                        "details": {
                            "type": "string",
                            "description": "Additional details for the step"
                        }
                    },
                    "required": ["description"]
                }
            },
            "plan": {
                "type": "string",
                "description": "Alternative: provide the entire plan as a text string (will be auto-parsed into structured steps)"
            }
        },
        "required": ["title"]
    }
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数，兼容plan文本格式"""
        title = kwargs.get("title")
        description = kwargs.get("description")
        steps = kwargs.get("steps")
        
        # 兼容：plan 参数可能是 dict（包含 title/description/steps）
        plan_arg = kwargs.get("plan")
        if isinstance(plan_arg, dict) and not title:
            title = plan_arg.get("title")
            if not description:
                description = plan_arg.get("description")
            if not steps:
                steps = plan_arg.get("steps")
        
        # 兼容：如果传入的是 plan 文本字符串而非结构化参数
        # LLM 有时会传 {"plan": "一整段计划文本"} 而非 title/description/steps
        if plan_arg and isinstance(plan_arg, str) and not title:
            # 自动从文本生成结构化参数
            lines = plan_arg.strip().split('\n')
            # 从第一行或 "##" 标题中提取 title
            title = "Auto-generated Plan"
            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith('#'):
                    title = line_stripped.lstrip('#').strip()
                    break
            description = plan_arg[:200] + ("..." if len(plan_arg) > 200 else "")
            
            # 从文本中提取步骤（识别 "### 数字", "- ", "1." 等格式）
            steps = []
            for line in lines:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                # 匹配常见步骤格式
                import re
                step_match = re.match(r'^(?:#{1,4}\s*)?(?:\d+[\.\)]\s*|[-*]\s+)(.+)', line_stripped)
                if step_match and len(step_match.group(1)) > 5:
                    step_desc = step_match.group(1).strip()
                    # 去掉末尾的冒号
                    step_desc = step_desc.rstrip(':')
                    if step_desc not in [s.get('description', '') for s in steps]:
                        steps.append({"description": step_desc})
            
            if not steps:
                # 如果无法提取步骤，创建一个通用步骤
                steps = [{"description": f"Execute the plan: {title}"}]
        
        # 兼容：只传了 steps 但没 title，自动生成
        if not title and steps and isinstance(steps, list) and len(steps) > 0:
            first_step = steps[0]
            if isinstance(first_step, dict) and first_step.get("description"):
                title = f"Plan: {first_step['description'][:50]}"
            else:
                title = f"Plan ({len(steps)} steps)"
        
        # 兼容：description 缺失时用 title 兜底
        if not description:
            description = title or "Implementation plan"
        
        if not title:
            raise ValueError("title parameter is required. Use format: {\"title\": \"...\", \"description\": \"...\", \"steps\": [{\"description\": \"...\"}]}")
        
        # 兼容：steps 缺失时创建默认步骤
        if not steps:
            steps = [{"description": f"Execute the plan: {title}"}]
        if not isinstance(steps, list):
            raise ValueError("steps must be a list")
        
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"step {i} must be a dictionary")
            if "description" not in step:
                raise ValueError(f"step {i} must have 'description' field")
        
        return {
            "title": str(title),
            "description": str(description),
            "steps": steps
        }
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """创建计划"""
        if PlanModeManager.get_mode() != PlanMode.PLAN:
            return {
                "success": False,
                "error": "Not in plan mode. Use enter_plan_mode first.",
                "message": "Must be in plan mode to create a plan"
            }
        
        plan = PlanModeManager.create_plan(
            input_data["title"],
            input_data["description"],
            input_data["steps"]
        )
        
        return {
            "success": True,
            "plan": plan.to_dict(),
            "message": f"Created plan: {plan.title}"
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None


class GetPlanTool(BaseTool[Dict[str, Any], Dict[str, Any]]):
    """
    获取计划工具
    
    获取当前计划
    """
    
    name = "get_plan"
    description = "Get the current plan details"
    input_params = []
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        return {}
    
    async def call(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """获取当前计划"""
        plan = PlanModeManager.get_current_plan()
        
        if not plan:
            return {
                "success": True,
                "plan": None,
                "message": "No current plan"
            }
        
        return {
            "success": True,
            "plan": plan.to_dict(),
            "mode": PlanModeManager.get_mode().value,
            "message": f"Current plan: {plan.title}"
        }
    
    def _extract_content(self, input_data: Dict[str, Any]) -> Optional[str]:
        return None
