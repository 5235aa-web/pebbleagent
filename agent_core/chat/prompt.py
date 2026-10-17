"""System prompt assembly."""


def render_project_path_instruction(project_path: str) -> str:
    return f"""

【重要：项目工作目录】
你的工作目录（项目文件夹）是：{project_path}

⚠️ 严格遵守以下规则：
1. 所有文件操作必须在这个目录下进行
2. 使用相对路径时，会自动相对于此目录解析
3. 绝对禁止访问此目录之外的文件（安全限制）
4. 执行Shell命令时，会自动在此目录下执行
5. 如果用户要求操作其他目录的文件，必须明确拒绝并告知你的工作目录

当前工作目录: {project_path}
"""


def render_usernote_prompt(content: str) -> str:
    if not content:
        return ""
    return f"""

<usernote>
{content}
</usernote>

你有能力通过 usernote_write 工具读取和修改 usernote 的内容。usernote 是用户用来记录重要上下文、决策、待办事项的便签，优先级高于对话历史。当用户要求你修改 usernote 时，调用 usernote_write 工具。"""


def compose_system_prompt(base: str, memory: str, project_path: str, usernote: str) -> str:
    return (
        base
        + (memory or "")
        + render_project_path_instruction(project_path)
        + render_usernote_prompt(usernote)
    )
