"""
Multi-Agents Module - Pebble多Agent系统

提供独立Agent聊天系统（系统B）功能

系统B: 独立Agent聊天系统 (Standalone Agent Chat System)
- 提供完全独立的Agent聊天界面
- 用户直接与专业Agent对话，不经过主Agent
"""

# Agent定义和加载（系统B依赖）
from .agent_loader import (
    AgentDefinition,
    load_agent_yaml,
    get_cached_agent,
    list_available_agents_from_yaml,
    get_agent_system_prompt,
)

__all__ = [
    # Agent定义
    'AgentDefinition',
    'load_agent_yaml',
    'get_cached_agent',
    'list_available_agents_from_yaml',
    'get_agent_system_prompt',
]

__version__ = "2.0.0"
