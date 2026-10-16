"""
Extension Tools Directory - 扩展工具目录

将自定义工具放在此目录下，每个 .py 文件定义一个工具。
文件需暴露 4 个约定属性：
- tool_name: str
- tool_description: str
- tool_parameters: dict (JSON Schema)
- async def execute(params, context=None) -> str
"""