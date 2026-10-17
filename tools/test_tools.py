"""
测试新迁移的工具

验证所有工具是否正确注册和执行
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tool_registry import ToolRegistry, auto_register_tools
from tools import (
    TodoListTool, TodoReadTool, TodoManager,
    ClarifyTool, QuestionManager,
    GrepTool, SearchCodebaseTool,
    GlobTool, LS,
    WebSearchTool,
    PlanModeEnterTool, PlanModeExitTool, CreatePlanTool, GetPlanTool,
    ToolSearchTool, ListToolsTool
)


def test_tool_registry():
    """测试工具注册"""
    print("=" * 60)
    print("测试工具注册")
    print("=" * 60)
    
    tools_to_register = [
        TodoListTool,
        TodoReadTool,
        ClarifyTool,
        GrepTool,
        SearchCodebaseTool,
        GlobTool,
        LS,
        WebSearchTool,
        PlanModeEnterTool,
        PlanModeExitTool,
        CreatePlanTool,
        GetPlanTool,
        ToolSearchTool,
        ListToolsTool
    ]
    
    for tool_class in tools_to_register:
        try:
            ToolRegistry.register(tool_class)
            print(f"[OK] 注册成功: {tool_class.name}")
        except Exception as e:
            print(f"[FAIL] 注册失败: {tool_class.name} - {e}")
    
    print(f"\n已注册工具数量: {len(ToolRegistry.list_tools())}")
    print(f"已注册工具列表: {ToolRegistry.list_tools()}")


async def test_todo_tool():
    """测试任务清单工具"""
    print("\n" + "=" * 60)
    print("测试 TodoListTool")
    print("=" * 60)
    
    tool = TodoListTool()
    
    test_todos = [
        {"id": "1", "content": "测试任务1", "status": "pending", "priority": "high"},
        {"id": "2", "content": "测试任务2", "status": "in_progress", "priority": "medium"},
        {"id": "3", "content": "测试任务3", "status": "completed", "priority": "low"}
    ]
    
    result = await tool.execute(todos=test_todos)
    print(f"执行结果: {result}")
    print(f"成功: {result.success}")
    
    read_tool = TodoReadTool()
    read_result = await read_tool.execute()
    print(f"读取结果: {read_result.result}")


async def test_grep_tool():
    """测试Grep工具"""
    print("\n" + "=" * 60)
    print("测试 GrepTool")
    print("=" * 60)
    
    tool = GrepTool()
    
    result = await tool.execute(
        pattern="import",
        path=".",
        output_mode="files_with_matches",
        head_limit=5
    )
    
    print(f"执行结果: {result}")
    print(f"成功: {result.success}")
    if result.result:
        print(f"找到文件: {result.result.get('filenames', [])[:3]}")


async def test_glob_tool():
    """测试Glob工具"""
    print("\n" + "=" * 60)
    print("测试 GlobTool")
    print("=" * 60)
    
    tool = GlobTool()
    
    result = await tool.execute(
        pattern="*.py",
        path="."
    )
    
    print(f"执行结果: {result}")
    print(f"成功: {result.success}")
    if result.result:
        print(f"找到文件数量: {result.result.get('numFiles', 0)}")
        print(f"文件列表(前5个): {result.result.get('filenames', [])[:5]}")

async def test_plan_mode_tool():
    """测试PlanMode工具"""
    print("\n" + "=" * 60)
    print("测试 PlanModeTool")
    print("=" * 60)
    
    enter_tool = PlanModeEnterTool()
    result = await enter_tool.execute()
    print(f"进入计划模式: {result.result}")
    
    create_tool = CreatePlanTool()
    plan_result = await create_tool.execute(
        title="测试计划",
        description="这是一个测试计划",
        steps=[
            {"description": "步骤1"},
            {"description": "步骤2"},
            {"description": "步骤3"}
        ]
    )
    print(f"创建计划: {plan_result.result}")
    
    get_tool = GetPlanTool()
    get_result = await get_tool.execute()
    print(f"获取计划: {get_result.result}")
    
    exit_tool = PlanModeExitTool()
    exit_result = await exit_tool.execute()
    print(f"退出计划模式: {exit_result.result}")


async def test_tool_search():
    """测试工具搜索"""
    print("\n" + "=" * 60)
    print("测试 ToolSearchTool")
    print("=" * 60)
    
    tool = ToolSearchTool()
    
    result = await tool.execute(query="file")
    print(f"搜索 'file': {result.result}")
    
    list_tool = ListToolsTool()
    list_result = await list_tool.execute()
    print(f"工具列表数量: {list_result.result.get('count', 0) if list_result.result else 0}")


async def main():
    """主测试函数"""
    print("\n" + "=" * 60)
    print("开始测试新迁移的工具")
    print("=" * 60)
    
    test_tool_registry()
    
    await test_todo_tool()
    await test_grep_tool()
    await test_glob_tool()
    await test_plan_mode_tool()
    await test_tool_search()
    
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
