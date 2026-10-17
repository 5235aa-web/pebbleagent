"""
Agent YAML Loader - Agent定义YAML加载器

支持从YAML文件加载Agent定义
"""

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)

# Agent配置目录
AGENTS_DIR = Path(__file__).parent / "agents"


@dataclass
class ContextStrategy:
    """上下文继承策略配置"""
    type: str = "recent_50"  # recent_50, recent_100, summary, none
    include_system_messages: bool = True
    filter_incomplete_tools: bool = True
    max_tokens: Optional[int] = None  # 最大token限制


@dataclass
class ToolPermissions:
    """工具权限配置"""
    allowed: List[str] = field(default_factory=list)
    disallowed: List[str] = field(default_factory=list)
    # 是否强制执行工具白名单/黑名单（默认 False：向后兼容，仅声明不生效）
    # enforce=True 时：allowed 非空 → 仅允许列表内工具；disallowed 非空 → 剔除列表内工具
    enforce: bool = False
    # 子agent创建孙子agent时，自动禁止的工具
    # 用于控制嵌套深度：孙子agent不能再委托
    deny_tools_for_children: List[str] = field(default_factory=list)


@dataclass
class AgentBehavior:
    """Agent行为配置"""
    professional_tone: bool = True
    auto_execute: bool = False
    verbosity: str = "detailed"  # minimal, moderate, detailed


@dataclass
class AgentDefinition:
    """
    Agent定义
    
    Agent定义结构
    """
    name: str
    description: str
    
    # 模型配置
    model: str = "inherit"  # inherit 或具体模型名
    temperature: float = 0.7
    
    # 上下文策略
    context_strategy: ContextStrategy = field(default_factory=ContextStrategy)
    
    # 工具权限
    tools: ToolPermissions = field(default_factory=ToolPermissions)
    
    # Skills
    skills: List[str] = field(default_factory=list)
    
    # 行为配置
    behavior: AgentBehavior = field(default_factory=AgentBehavior)
    
    # 系统提示词
    system_prompt: str = ""
    system_prompt_file: Optional[str] = None
    
    # 元数据
    meta: Dict[str, Any] = field(default_factory=dict)
    
    # 内部使用
    _config_path: Optional[Path] = None


def load_agent_yaml(agent_name: str) -> Optional[AgentDefinition]:
    """
    从YAML文件加载Agent定义
    
    Args:
        agent_name: Agent名称（不含.yaml后缀）
    
    Returns:
        AgentDefinition 或 None（如果文件不存在或解析失败）
    """
    yaml_path = AGENTS_DIR / f"{agent_name}.yaml"
    
    if not yaml_path.exists():
        logger.warning(f"[AgentLoader] Agent config not found: {yaml_path}")
        return None
    
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        if not config:
            logger.error(f"[AgentLoader] Empty config file: {yaml_path}")
            return None
        
        # 解析系统提示词
        system_prompt = ""
        system_prompt_file = config.get('system_prompt_file')
        
        if system_prompt_file:
            # 从文件加载prompt
            prompt_path = yaml_path.parent / system_prompt_file
            if prompt_path.exists():
                with open(prompt_path, 'r', encoding='utf-8') as f:
                    system_prompt = f.read()
                logger.info(f"[AgentLoader] Loaded system prompt from {prompt_path}")
            else:
                logger.warning(f"[AgentLoader] System prompt file not found: {prompt_path}")
        
        # 如果没有文件，检查是否有内联prompt
        if not system_prompt and 'system_prompt' in config:
            system_prompt = config['system_prompt']
        
        # 解析工具权限
        tools_config = config.get('tools', {})
        tools = ToolPermissions(
            allowed=tools_config.get('allowed', []),
            disallowed=tools_config.get('disallowed', []),
            enforce=bool(tools_config.get('enforce', False)),
            deny_tools_for_children=tools_config.get('deny_tools_for_children', [])
        )
        
        # 解析上下文策略
        context_config = config.get('context_strategy', {})
        context_strategy = ContextStrategy(
            type=context_config.get('type', 'recent_50'),
            include_system_messages=context_config.get('include_system_messages', True),
            filter_incomplete_tools=context_config.get('filter_incomplete_tools', True),
            max_tokens=context_config.get('max_tokens')
        )
        
        # 解析行为配置
        behavior_config = config.get('behavior', {})
        behavior = AgentBehavior(
            professional_tone=behavior_config.get('professional_tone', True),
            auto_execute=behavior_config.get('auto_execute', False),
            verbosity=behavior_config.get('verbosity', 'detailed')
        )
        
        # 创建Agent定义
        agent_def = AgentDefinition(
            name=config['name'],
            description=config['description'],
            model=config.get('model', 'inherit'),
            temperature=config.get('temperature', 0.7),
            context_strategy=context_strategy,
            tools=tools,
            skills=config.get('skills', []),
            behavior=behavior,
            system_prompt=system_prompt,
            system_prompt_file=system_prompt_file,
            meta=config.get('meta', {}),
            _config_path=yaml_path
        )
        
        logger.info(f"[AgentLoader] Loaded agent: {agent_def.name}")
        return agent_def
        
    except yaml.YAMLError as e:
        logger.error(f"[AgentLoader] YAML parse error in {yaml_path}: {e}")
        return None
    except KeyError as e:
        logger.error(f"[AgentLoader] Missing required field in {yaml_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"[AgentLoader] Error loading {yaml_path}: {e}")
        return None


def list_available_agents_from_yaml() -> List[Dict[str, str]]:
    """
    列出所有可用的YAML Agent配置
    
    Returns:
        Agent列表，每项包含name和description
    """
    agents = []
    
    if not AGENTS_DIR.exists():
        logger.warning(f"[AgentLoader] Agents directory not found: {AGENTS_DIR}")
        return agents
    
    for yaml_file in AGENTS_DIR.glob("*.yaml"):
        try:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            if config and 'name' in config:
                agents.append({
                    'name': config['name'],
                    'description': config.get('description', 'No description'),
                    'file': yaml_file.name
                })
        except Exception as e:
            logger.warning(f"[AgentLoader] Error reading {yaml_file}: {e}")
    
    return agents


def list_available_agents() -> List[Dict[str, str]]:
    """
    列出所有可用的Agent配置（list_available_agents_from_yaml的别名）
    
    Returns:
        Agent列表，每项包含name和description
    """
    return list_available_agents_from_yaml()


def get_agent_system_prompt(agent_def: AgentDefinition) -> str:
    """
    获取Agent的系统提示词
    
    如果Agent有预加载skills，将其附加到prompt中
    """
    prompt = agent_def.system_prompt
    
    # 如果有skills，加载并附加
    if agent_def.skills:
        try:
            import sys
            parent_dir = Path(__file__).parent.parent
            if str(parent_dir) not in sys.path:
                sys.path.insert(0, str(parent_dir))
            from skills_loader import load_skill
            
            skill_sections = []
            for skill_name in agent_def.skills:
                content = load_skill(skill_name)
                if content:
                    skill_sections.append(f"=== {skill_name} ===\n{content}")
                    logger.debug(f"[AgentLoader] Preloaded skill: {skill_name}")
            
            if skill_sections:
                prompt += "\n\n[预加载技能参考]\n" + "\n\n".join(skill_sections)
                
        except Exception as e:
            logger.error(f"[AgentLoader] Error preloading skills: {e}")
    
    return prompt


# 缓存已加载的Agent定义
_agent_cache: Dict[str, AgentDefinition] = {}


def get_cached_agent(agent_name: str) -> Optional[AgentDefinition]:
    """获取缓存的Agent定义（如未加载则加载）"""
    if agent_name not in _agent_cache:
        _agent_cache[agent_name] = load_agent_yaml(agent_name)
    return _agent_cache.get(agent_name)


def clear_agent_cache():
    """清除Agent缓存"""
    _agent_cache.clear()
    logger.info("[AgentLoader] Agent cache cleared")


def filter_tools_for_agent(
    tools: List[Dict[str, Any]],
    agent_def: Optional[AgentDefinition]
) -> List[Dict[str, Any]]:
    """
    按 Agent 定义的工具权限过滤工具 schema 列表（OpenAI tools 格式）。

    仅当 agent_def.tools.enforce 为 True 时生效：
    - allowed 非空 → 白名单模式（仅保留列表内的工具）
    - disallowed 非空 → 黑名单剔除
    未启用 enforce 或 agent_def 为 None → 原样返回（向后兼容）。

    用途：确保被禁止的工具说明不会上传给 LLM。
    """
    if not agent_def or not agent_def.tools.enforce:
        return tools

    perms = agent_def.tools
    filtered: List[Dict[str, Any]] = []
    for tool in tools:
        name = (tool.get("function") or {}).get("name", "")
        if perms.allowed and name not in perms.allowed:
            continue
        if perms.disallowed and name in perms.disallowed:
            continue
        filtered.append(tool)
    return filtered


def check_agent_tool_policy(agent_name: str, tool_name: str) -> Optional[str]:
    """
    执行层工具策略检查：判断工具是否被 Agent 的权限配置允许执行。

    返回 None 表示放行；返回字符串表示拒绝原因。
    未找到 Agent、未启用 enforce 或 agent_name 为空 → 放行（向后兼容）。

    用途：即使 LLM 幻觉调用了未注入 schema 的工具，也在执行入口被拒绝。
    """
    if not agent_name:
        return None
    agent_def = get_cached_agent(agent_name)
    if not agent_def or not agent_def.tools.enforce:
        return None

    perms = agent_def.tools
    if perms.allowed and tool_name not in perms.allowed:
        return f"Tool '{tool_name}' is not in the allowlist of agent '{agent_name}'"
    if perms.disallowed and tool_name in perms.disallowed:
        return f"Tool '{tool_name}' is disabled for agent '{agent_name}'"
    return None
