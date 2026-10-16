"""
统一工具系统

参照Claude Code设计，提供：
- 统一的工具基类
- Pydantic Schema验证
- 权限系统集成
- 标准化描述
- 结果序列化
"""

from .base_tool import BaseTool, ToolResult, ToolStatus, PermissionResult
from .tool_registry import ToolRegistry

from .todo_tool import TodoWriteTool, TodoReadTool, TodoManager, TodoItem, TodoStatus
from .ask_question_tool import AskUserQuestionTool, QuestionManager, Question, QuestionOption, QuestionAnswer
from .grep_tool import GrepTool, SearchCodebaseTool
from .glob_tool import GlobTool, LS
from .web_fetch_tool import WebSearchTool
from .plan_mode_tool import (
    EnterPlanModeTool, 
    ExitPlanModeTool, 
    CreatePlanTool, 
    GetPlanTool,
    PlanModeManager, 
    PlanMode, 
    Plan, 
    PlanStep
)
from .tool_search_tool import ToolSearchTool, ListToolsTool
from .execute_code_tool import ExecuteCodeTool, ExecutePythonTool, ExecuteShellTool
from .file_tools import ReadFileTool, WriteFileTool, EditFileTool, SearchFilesTool
from .skill_tools import LoadSkillTool, ListSkillsTool

# UIA3 工具（Windows UI Automation）
from .uia_tools import UIAGetElementsTool, UIAFindElementTool, UIAGetWindowListTool

# 新迁移的工具（从 claude-code-main）
from .powershell_tool import PowerShellTool, PowerShellEdition
from .notebook_edit_tool import NotebookEditTool
from .brief_tool import BriefTool

__all__ = [
    'BaseTool',
    'ToolResult', 
    'ToolStatus',
    'PermissionResult',
    'ToolRegistry',
    'TodoWriteTool',
    'TodoReadTool',
    'TodoManager',
    'TodoItem',
    'TodoStatus',
    'AskUserQuestionTool',
    'QuestionManager',
    'Question',
    'QuestionOption',
    'QuestionAnswer',
    'GrepTool',
    'SearchCodebaseTool',
    'GlobTool',
    'LS',
    'WebSearchTool',
    'EnterPlanModeTool',
    'ExitPlanModeTool',
    'CreatePlanTool',
    'GetPlanTool',
    'PlanModeManager',
    'PlanMode',
    'Plan',
    'PlanStep',
    'ToolSearchTool',
    'ListToolsTool',
    'ExecuteCodeTool',
    'ExecutePythonTool',
    'ExecuteShellTool',
    'ReadFileTool',
    'WriteFileTool',
    'EditFileTool',
    'SearchFilesTool',
    'LoadSkillTool',
    'ListSkillsTool',
    # UIA3 工具
    'UIAGetElementsTool',
    'UIAFindElementTool',
    'UIAGetWindowListTool',
    # 新迁移的工具（从 claude-code-main）
    'PowerShellTool',
    'PowerShellEdition',
    'NotebookEditTool',
    'BriefTool',
]
