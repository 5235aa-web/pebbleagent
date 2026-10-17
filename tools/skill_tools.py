"""
技能工具

提供技能加载和列表功能
"""
from .base_tool import BaseTool
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class LoadSkillTool(BaseTool[Dict[str, Any], str]):
    """
    技能加载工具
    """
    
    name = "load_skill"
    description = "加载指定技能的完整内容"
    input_params = ["name"]
    requires_permission = False
    danger_level = "low"
    
    def validate_input(self, **kwargs) -> Dict[str, Any]:
        """验证输入参数"""
        name = kwargs.get('name')
        if not name:
            raise ValueError("name parameter is required")
        return {'name': str(name)}
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """加载技能"""
        name = input_data['name']
        
        try:
            from skills_loader import load_skill
            content = load_skill(name)
            if content:
                return content
            return f"Error: Skill '{name}' not found"
        except ImportError:
            return "Error: skills_loader module not found"
        except Exception as e:
            return f"Error loading skill: {str(e)}"


class ListSkillsTool(BaseTool[Dict[str, Any], str]):
    """
    列出技能工具
    """
    
    name = "list_skills"
    description = "列出所有可用技能"
    input_params = []
    requires_permission = False
    danger_level = "low"
    
    async def call(self, input_data: Dict[str, Any]) -> str:
        """列出所有技能"""
        try:
            from skills_loader import list_skills
            skills = list_skills()
            if skills:
                lines = ["## Available Skills\n"]
                for s in skills:
                    lines.append(f"- **{s['name']}**: {s['description']}")
                return "\n".join(lines)
            return "No skills available"
        except ImportError:
            return "Error: skills_loader module not found"
        except Exception as e:
            return f"Error listing skills: {str(e)}"