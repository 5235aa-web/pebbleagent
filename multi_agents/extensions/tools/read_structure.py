"""
Extension Tool: read_structure - 读取项目架构文档

当 Agent 首次接触一个项目时，调用此工具读取项目根目录的 Structure.md，
快速了解项目目录结构、技术栈、架构约定等关键信息。
后续工作中如果需要回顾架构，也可以随时调用。
"""

import os
from typing import Dict, Any, Optional


tool_name = "read_structure"

tool_description = (
    "读取项目根目录下的 Structure.md 文件，了解项目的整体架构。"
    "【重要】当你首次接触一个项目时，务必先调用此工具了解项目结构，"
    "包括目录组织、技术栈、架构约定等信息，这有助于后续工作不偏离项目规范。"
    "后续工作中如需回顾架构也可再次调用。"
    "返回 Structure.md 的完整内容。"
)

tool_parameters = {
    "type": "object",
    "properties": {
        "project_path": {
            "type": "string",
            "description": "项目根目录路径（可选）。不传则自动使用当前项目路径。"
        }
    },
    "required": []
}


async def execute(params: Dict[str, Any], context: Optional[Any] = None) -> str:
    """
    读取 Structure.md 文件

    Args:
        params: {"project_path": "可选的项目路径"}
        context: 执行上下文（包含 project_path 等信息）

    Returns:
        Structure.md 内容或错误信息
    """
    # 确定项目路径：优先参数 > context > 当前目录
    project_path = params.get("project_path")
    if not project_path:
        if context and hasattr(context, "project_path") and context.project_path:
            project_path = context.project_path
        else:
            project_path = os.getcwd()

    # 解析并规范化路径
    project_path = os.path.abspath(project_path)

    # 检查项目目录是否存在
    if not os.path.isdir(project_path):
        return f"Error: 项目路径不存在或不是目录: {project_path}"

    # 尝试读取 Structure.md
    structure_path = os.path.join(project_path, "Structure.md")

    if not os.path.isfile(structure_path):
        # 也检查小写形式
        structure_path_lower = os.path.join(project_path, "structure.md")
        if os.path.isfile(structure_path_lower):
            structure_path = structure_path_lower
        else:
            return (
                f"提示: 项目根目录下未找到 Structure.md 文件。\n"
                f"路径: {project_path}\n\n"
                f"你可以尝试用其他方式了解项目结构，例如：\n"
                f"- 用 read_file 读取 README.md\n"
                f"- 用 list_directory 查看项目目录结构\n"
                f"- 用 glob 搜索特定类型的文件"
            )

    try:
        with open(structure_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return f"提示: {structure_path} 文件内容为空。"

        # 添加文件来源信息
        rel_path = os.path.relpath(structure_path, project_path)
        return (
            f"📁 项目架构文档 ({rel_path}):\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{content}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

    except PermissionError:
        return f"Error: 无权限读取: {structure_path}"
    except UnicodeDecodeError:
        return f"Error: 文件编码不是 UTF-8，无法读取: {structure_path}"
    except Exception as e:
        return f"Error: 读取失败: {type(e).__name__}: {e}"
