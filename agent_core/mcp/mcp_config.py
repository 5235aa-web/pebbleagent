"""
MCP Config - MCP 配置管理

功能：
- 多作用域配置加载（user/project/local/dynamic/enterprise）
- 配置验证和解析
- 服务器添加/删除
- 服务器启用/禁用
- 环境变量展开
- 策略过滤（allowlist/denylist）
- 插件去重

配置优先级（从低到高）：
1. user (~/.pebble/mcp_config.json)
2. project (.mcp.json，从 CWD 向上遍历)
3. local (项目设置数据库)
4. dynamic (--mcp-config 命令行参数)
5. enterprise (托管配置)
"""

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from copy import deepcopy

from .mcp_types import (
    ConfigScope,
    TransportType,
    McpStdioServerConfig,
    McpSSEServerConfig,
    McpHTTPServerConfig,
    McpWebSocketServerConfig,
    McpSdkServerConfig,
    McpServerConfig,
    ScopedMcpServerConfig,
    McpJsonConfig,
    get_config_type,
)

logger = logging.getLogger(__name__)

# ==================== 默认配置路径 ====================

DEFAULT_USER_CONFIG_DIR = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    ".pebble"
)
DEFAULT_MCP_CONFIG_FILE = "mcp_config.json"


# ==================== 环境变量展开 ====================

def expand_env_vars(value: str) -> Tuple[str, List[str]]:
    """
    展开字符串中的环境变量引用
    
    支持格式：${VAR_NAME} 或 $VAR_NAME
    
    Returns:
        (展开后的字符串, 缺失的变量名列表)
    """
    missing_vars: List[str] = []
    
    def replacer(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        if var_name in os.environ:
            return os.environ[var_name]
        missing_vars.append(var_name)
        return match.group(0)  # 保留原始引用
    
    # 匹配 ${VAR} 和 $VAR
    result = re.sub(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)', replacer, value)
    return result, missing_vars


def expand_env_vars_in_config(config: McpServerConfig) -> McpServerConfig:
    """展开配置中的所有环境变量"""
    config_type = get_config_type(config)
    
    if config_type in (None, "stdio"):
        cfg = config  # type: McpStdioServerConfig
        result = deepcopy(cfg)
        result.command, _ = expand_env_vars(cfg.command)
        result.args = [expand_env_vars(a)[0] for a in (cfg.args or [])]
        if cfg.env:
            result.env = {k: expand_env_vars(v)[0] for k, v in cfg.env.items()}
        return result
    
    elif config_type in ("sse", "http", "ws"):
        if hasattr(config, 'url'):
            cfg = config
            result = deepcopy(cfg)
            result.url, _ = expand_env_vars(cfg.url)  # type: ignore
            if cfg.headers:  # type: ignore
                result.headers = {k: expand_env_vars(v)[0] for k, v in cfg.headers.items()}  # type: ignore
            return result
    
    return deepcopy(config)


# ==================== 配置验证 ====================

def validate_server_config(name: str, config: Any) -> Tuple[Optional[McpServerConfig], List[str]]:
    """
    验证服务器配置
    
    Returns:
        (验证后的配置, 错误列表)
    """
    errors: List[str] = []
    
    # 检查名称合法性
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        errors.append(f"Invalid name '{name}'. Names can only contain letters, numbers, hyphens, and underscores.")
        return None, errors
    
    # 检查配置是否有效
    if not isinstance(config, dict):
        errors.append("Config must be a dictionary")
        return None, errors
    
    config_type = config.get("type", "stdio")
    
    if config_type in (None, "stdio"):
        if "command" not in config or not config["command"]:
            errors.append("Command cannot be empty for stdio server")
    elif config_type in ("sse", "http", "ws"):
        if "url" not in config or not config["url"]:
            errors.append(f"URL cannot be empty for {config_type} server")
    elif config_type == "sdk":
        if "name" not in config or not config["name"]:
            errors.append("Name cannot be empty for SDK server")
    elif config_type == "claudeai-proxy":
        if "url" not in config or "id" not in config:
            errors.append("URL and id are required for claudeai-proxy server")
    else:
        errors.append(f"Unsupported server type: {config_type}")
    
    if errors:
        return None, errors
    
    # 构造配置对象
    try:
        if config_type in (None, "stdio"):
            validated = McpStdioServerConfig(
                command=config.get("command", ""),
                args=config.get("args", []),
                env=config.get("env"),
                type=config_type if config_type else None,
            )
        elif config_type == "sse":
            validated = McpSSEServerConfig(
                type="sse",
                url=config.get("url", ""),
                headers=config.get("headers"),
            )
        elif config_type == "http":
            validated = McpHTTPServerConfig(
                type="http",
                url=config.get("url", ""),
                headers=config.get("headers"),
            )
        elif config_type == "ws":
            validated = McpWebSocketServerConfig(
                type="ws",
                url=config.get("url", ""),
                headers=config.get("headers"),
            )
        elif config_type == "sdk":
            validated = McpSdkServerConfig(
                type="sdk",
                name=config.get("name", ""),
            )
        else:
            return None, errors
        
        return validated, []
    except Exception as e:
        errors.append(f"Failed to create config object: {e}")
        return None, errors


# ==================== Main 配置管理器 ====================

class McpConfigManager:
    """
    MCP 配置管理器
    
    管理所有作用域的 MCP 服务器配置。
    """
    
    def __init__(
        self,
        user_config_dir: str = DEFAULT_USER_CONFIG_DIR,
        project_path: Optional[str] = None
    ):
        self._user_config_dir = user_config_dir
        self._project_path = project_path or os.getcwd()
        
        # Dynamic configs (from --mcp-config CLI flag)
        self._dynamic_configs: Dict[str, ScopedMcpServerConfig] = {}
        
        # Enterprise configs
        self._enterprise_configs: Dict[str, ScopedMcpServerConfig] = {}
        
        # Lock for thread safety
        self._lock = threading.RLock()
        
        # Ensure config directory exists
        os.makedirs(user_config_dir, exist_ok=True)
    
    def set_project_path(self, path: str):
        """设置项目路径"""
        self._project_path = path
    
    def set_dynamic_configs(self, configs: Dict[str, ScopedMcpServerConfig]):
        """设置动态配置"""
        with self._lock:
            self._dynamic_configs = deepcopy(configs)
    
    # ==================== 配置加载 ====================
    
    def _get_user_config_path(self) -> str:
        """获取用户配置文件路径"""
        return os.path.join(self._user_config_dir, DEFAULT_MCP_CONFIG_FILE)
    
    def _load_user_configs(self) -> Dict[str, ScopedMcpServerConfig]:
        """加载用户作用域配置"""
        config_path = self._get_user_config_path()
        servers: Dict[str, ScopedMcpServerConfig] = {}
        
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                mcp_servers = data.get("mcpServers", {})
                for name, config in mcp_servers.items():
                    validated, errors = validate_server_config(name, config)
                    if validated and not errors:
                        expanded = expand_env_vars_in_config(validated)
                        servers[name] = ScopedMcpServerConfig(
                            config=expanded,
                            scope=ConfigScope.USER
                        )
                    elif errors:
                        logger.warning(f"Invalid user MCP config '{name}': {errors}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse user MCP config: {e}")
        except Exception as e:
            logger.error(f"Error loading user MCP configs: {e}")
        
        return servers
    
    def _load_project_configs(self) -> Dict[str, ScopedMcpServerConfig]:
        """加载项目作用域配置（.mcp.json，从 CWD 向上遍历）"""
        servers: Dict[str, ScopedMcpServerConfig] = {}
        
        # 从 CWD 向上遍历查找 .mcp.json
        current_dir = Path(self._project_path).resolve()
        roots = [current_dir] + list(current_dir.parents)
        
        # 从根向 CWD 处理（越近的优先级越高）
        for directory in reversed(roots):
            mcp_json_path = directory / ".mcp.json"
            if not mcp_json_path.exists():
                continue
            
            try:
                with open(mcp_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                mcp_servers = data.get("mcpServers", {})
                for name, config in mcp_servers.items():
                    validated, errors = validate_server_config(name, config)
                    if validated and not errors:
                        expanded = expand_env_vars_in_config(validated)
                        servers[name] = ScopedMcpServerConfig(
                            config=expanded,
                            scope=ConfigScope.PROJECT
                        )
                    elif errors:
                        logger.warning(f"Invalid project MCP config '{name}' at {mcp_json_path}: {errors}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse {mcp_json_path}: {e}")
            except Exception as e:
                logger.error(f"Error loading {mcp_json_path}: {e}")
        
        return servers
    
    def _load_local_configs(self) -> Dict[str, ScopedMcpServerConfig]:
        """加载本地项目配置（项目设置中的 MCP 配置）"""
        # 本地配置存储在项目的 .pebble 目录下
        local_config_dir = Path(self._project_path) / ".pebble"
        local_config_path = local_config_dir / "mcp_config.json"
        servers: Dict[str, ScopedMcpServerConfig] = {}
        
        try:
            if local_config_path.exists():
                with open(local_config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                mcp_servers = data.get("mcpServers", {})
                for name, config in mcp_servers.items():
                    validated, errors = validate_server_config(name, config)
                    if validated and not errors:
                        expanded = expand_env_vars_in_config(validated)
                        servers[name] = ScopedMcpServerConfig(
                            config=expanded,
                            scope=ConfigScope.LOCAL
                        )
                    elif errors:
                        logger.warning(f"Invalid local MCP config '{name}': {errors}")
        except Exception as e:
            pass  # 本地配置不存在是正常的
        
        return servers
    
    def get_all_configs(self) -> Dict[str, ScopedMcpServerConfig]:
        """
        获取所有作用域的配置（合并后）
        
        优先级（从低到高）：
        user < project < local < dynamic < enterprise
        """
        with self._lock:
            # 按优先级合并
            configs: Dict[str, ScopedMcpServerConfig] = {}
            
            # 1. User
            configs.update(self._load_user_configs())
            
            # 2. Project
            configs.update(self._load_project_configs())
            
            # 3. Local
            configs.update(self._load_local_configs())
            
            # 4. Dynamic
            configs.update(self._dynamic_configs)
            
            # 5. Enterprise
            configs.update(self._enterprise_configs)
            
            return configs
    
    def get_enabled_configs(self) -> Dict[str, ScopedMcpServerConfig]:
        """获取启用的服务器配置"""
        all_configs = self.get_all_configs()
        enabled: Dict[str, ScopedMcpServerConfig] = {}
        
        for name, config in all_configs.items():
            if not self.is_server_disabled(name):
                enabled[name] = config
        
        return enabled
    
    def get_config_by_name(self, name: str) -> Optional[ScopedMcpServerConfig]:
        """按名称获取配置"""
        # 优先级: enterprise > local > project > user
        all_configs = self.get_all_configs()
        
        # Enterprise 优先
        enterprise_configs = self._enterprise_configs
        if name in enterprise_configs:
            return enterprise_configs[name]
        
        # Local
        local_configs = self._load_local_configs()
        if name in local_configs:
            return local_configs[name]
        
        # Project
        project_configs = self._load_project_configs()
        if name in project_configs:
            return project_configs[name]
        
        # User
        user_configs = self._load_user_configs()
        if name in user_configs:
            return user_configs[name]
        
        return all_configs.get(name)
    
    # ==================== 配置修改 ====================
    
    def add_server(self, name: str, config: McpServerConfig, scope: ConfigScope = ConfigScope.USER) -> bool:
        """
        添加 MCP 服务器
        
        Args:
            name: 服务器名称
            config: 服务器配置
            scope: 配置作用域
        
        Returns:
            是否添加成功
        """
        with self._lock:
            # 检查是否已存在
            existing = self.get_config_by_name(name)
            if existing and existing.scope == scope:
                logger.warning(f"MCP server '{name}' already exists in {scope.value} scope")
                return False
            
            if scope == ConfigScope.USER:
                return self._add_to_user_scope(name, config)
            elif scope == ConfigScope.PROJECT:
                return self._add_to_project_scope(name, config)
            elif scope == ConfigScope.LOCAL:
                return self._add_to_local_scope(name, config)
            elif scope == ConfigScope.DYNAMIC:
                self._dynamic_configs[name] = ScopedMcpServerConfig(
                    config=config,
                    scope=scope
                )
                return True
            else:
                logger.error(f"Cannot add MCP server to {scope.value} scope")
                return False
    
    def _add_to_user_scope(self, name: str, config: McpServerConfig) -> bool:
        """添加到用户作用域"""
        config_path = self._get_user_config_path()
        try:
            data = {}
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
            if "mcpServers" not in data:
                data["mcpServers"] = {}
            
            # 转换为可序列化的字典
            config_dict = {}
            for key, value in config.__dict__.items():
                if value is not None and key != "type":
                    config_dict[key] = value
            if hasattr(config, 'type') and config.type:  # type: ignore
                config_dict["type"] = config.type  # type: ignore
            else:
                config_dict["type"] = "stdio"
            
            data["mcpServers"][name] = config_dict
            
            # 原子写入
            temp_path = config_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, config_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to add MCP server '{name}' to user scope: {e}")
            return False
    
    def _add_to_project_scope(self, name: str, config: McpServerConfig) -> bool:
        """添加到项目作用域（.mcp.json）"""
        mcp_json_path = os.path.join(self._project_path, ".mcp.json")
        try:
            data = {}
            if os.path.exists(mcp_json_path):
                with open(mcp_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
            if "mcpServers" not in data:
                data["mcpServers"] = {}
            
            config_dict = {}
            for key, value in config.__dict__.items():
                if value is not None and key != "type":
                    config_dict[key] = value
            if hasattr(config, 'type') and config.type:  # type: ignore
                config_dict["type"] = config.type  # type: ignore
            else:
                config_dict["type"] = "stdio"
            
            data["mcpServers"][name] = config_dict
            
            temp_path = mcp_json_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, mcp_json_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to add MCP server '{name}' to project scope: {e}")
            return False
    
    def _add_to_local_scope(self, name: str, config: McpServerConfig) -> bool:
        """添加到本地项目作用域"""
        local_config_dir = os.path.join(self._project_path, ".pebble")
        os.makedirs(local_config_dir, exist_ok=True)
        local_config_path = os.path.join(local_config_dir, "mcp_config.json")
        
        try:
            data = {}
            if os.path.exists(local_config_path):
                with open(local_config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
            if "mcpServers" not in data:
                data["mcpServers"] = {}
            
            config_dict = {}
            for key, value in config.__dict__.items():
                if value is not None and key != "type":
                    config_dict[key] = value
            if hasattr(config, 'type') and config.type:  # type: ignore
                config_dict["type"] = config.type  # type: ignore
            else:
                config_dict["type"] = "stdio"
            
            data["mcpServers"][name] = config_dict
            
            temp_path = local_config_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, local_config_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to add MCP server '{name}' to local scope: {e}")
            return False
    
    def remove_server(self, name: str, scope: Optional[ConfigScope] = None) -> bool:
        """
        删除 MCP 服务器
        
        Args:
            name: 服务器名称
            scope: 配置作用域（None 表示在所有作用域中删除）
        """
        with self._lock:
            if scope is None:
                # 尝试所有作用域
                for s in [ConfigScope.USER, ConfigScope.PROJECT, ConfigScope.LOCAL]:
                    if self.remove_server(name, s):
                        return True
                return False
            
            if scope == ConfigScope.USER:
                return self._remove_from_user_scope(name)
            elif scope == ConfigScope.PROJECT:
                return self._remove_from_project_scope(name)
            elif scope == ConfigScope.LOCAL:
                return self._remove_from_local_scope(name)
            elif scope == ConfigScope.DYNAMIC:
                if name in self._dynamic_configs:
                    del self._dynamic_configs[name]
                    return True
                return False
            else:
                return False
    
    def _remove_from_user_scope(self, name: str) -> bool:
        """从用户作用域删除"""
        config_path = self._get_user_config_path()
        try:
            if not os.path.exists(config_path):
                return False
            
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if name not in data.get("mcpServers", {}):
                return False
            
            del data["mcpServers"][name]
            
            temp_path = config_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, config_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to remove MCP server '{name}' from user scope: {e}")
            return False
    
    def _remove_from_project_scope(self, name: str) -> bool:
        """从项目作用域删除"""
        mcp_json_path = os.path.join(self._project_path, ".mcp.json")
        try:
            if not os.path.exists(mcp_json_path):
                return False
            
            with open(mcp_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if name not in data.get("mcpServers", {}):
                return False
            
            del data["mcpServers"][name]
            
            temp_path = mcp_json_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, mcp_json_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to remove MCP server '{name}' from project scope: {e}")
            return False
    
    def _remove_from_local_scope(self, name: str) -> bool:
        """从本地作用域删除"""
        local_config_path = os.path.join(self._project_path, ".pebble", "mcp_config.json")
        try:
            if not os.path.exists(local_config_path):
                return False
            
            with open(local_config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if name not in data.get("mcpServers", {}):
                return False
            
            del data["mcpServers"][name]
            
            temp_path = local_config_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, local_config_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to remove MCP server '{name}' from local scope: {e}")
            return False
    
    # ==================== 启用/禁用 ====================
    
    def _get_disabled_list_path(self) -> str:
        """获取禁用列表文件路径"""
        return os.path.join(self._project_path, ".pebble", "disabled_mcp_servers.json")
    
    def is_server_disabled(self, name: str) -> bool:
        """检查服务器是否被禁用"""
        disabled_path = self._get_disabled_list_path()
        try:
            if os.path.exists(disabled_path):
                with open(disabled_path, 'r', encoding='utf-8') as f:
                    disabled_list = json.load(f)
                    return name in disabled_list
        except Exception:
            pass
        return False
    
    def set_server_enabled(self, name: str, enabled: bool):
        """设置服务器启用/禁用状态"""
        disabled_path = self._get_disabled_list_path()
        os.makedirs(os.path.dirname(disabled_path), exist_ok=True)
        
        try:
            disabled_list = []
            if os.path.exists(disabled_path):
                with open(disabled_path, 'r', encoding='utf-8') as f:
                    disabled_list = json.load(f)
            
            if enabled:
                disabled_list = [n for n in disabled_list if n != name]
            else:
                if name not in disabled_list:
                    disabled_list.append(name)
            
            temp_path = disabled_path + ".tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(disabled_list, f, indent=2)
            os.replace(temp_path, disabled_path)
            
        except Exception as e:
            logger.error(f"Failed to set MCP server '{name}' enabled={enabled}: {e}")


# ==================== 用户覆盖配置（~/.pebble/.mcp.json） ====================

def build_user_override_configs() -> Dict[str, ScopedMcpServerConfig]:
    """
    读取用户数据目录的 .mcp.json（~/.pebble/.mcp.json），构造 dynamic 层配置。

    用途：UI 中填写的密钥（如 GitHub Token）持久化于该文件，以 dynamic scope
    （优先级高于 project/local）注入，覆盖内嵌配置中的占位符。
    与 _load_user_configs 的区别：数据源为 .mcp.json（用户可见的运行时覆盖文件），
    而非 mcp_config.json（Claude 兼容的 user scope）。
    """
    _home = os.environ.get("PEBBLE_HOME") or DEFAULT_USER_CONFIG_DIR
    config_path = os.path.join(_home, ".mcp.json")
    overrides: Dict[str, ScopedMcpServerConfig] = {}
    try:
        if not os.path.exists(config_path):
            return overrides
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, config in (data.get("mcpServers") or {}).items():
            validated, errors = validate_server_config(name, config)
            if validated and not errors:
                expanded = expand_env_vars_in_config(validated)
                overrides[name] = ScopedMcpServerConfig(
                    config=expanded,
                    scope=ConfigScope.DYNAMIC,
                )
            elif errors:
                logger.warning(f"Invalid user override MCP config '{name}': {errors}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse user override .mcp.json: {e}")
    except Exception as e:
        logger.error(f"Error loading user override .mcp.json: {e}")
    return overrides


# ==================== 全局实例 ====================

_config_manager: Optional[McpConfigManager] = None


def get_mcp_config_manager() -> McpConfigManager:
    """获取全局 MCP 配置管理器"""
    global _config_manager
    if _config_manager is None:
        _config_manager = McpConfigManager()
    return _config_manager


# ==================== 便捷函数 ====================

def add_mcp_config(name: str, config: Dict[str, Any], scope: ConfigScope = ConfigScope.USER) -> bool:
    """添加 MCP 服务器配置"""
    manager = get_mcp_config_manager()
    validated, errors = validate_server_config(name, config)
    if not validated:
        logger.error(f"Failed to validate MCP config '{name}': {errors}")
        return False
    return manager.add_server(name, validated, scope)


def remove_mcp_config(name: str, scope: Optional[ConfigScope] = None) -> bool:
    """删除 MCP 服务器配置"""
    manager = get_mcp_config_manager()
    return manager.remove_server(name, scope)


def get_mcp_config_by_name(name: str) -> Optional[ScopedMcpServerConfig]:
    """按名称获取 MCP 配置"""
    manager = get_mcp_config_manager()
    return manager.get_config_by_name(name)


def get_all_mcp_configs() -> Dict[str, ScopedMcpServerConfig]:
    """获取所有 MCP 配置"""
    manager = get_mcp_config_manager()
    return manager.get_all_configs()


def is_mcp_server_disabled(name: str) -> bool:
    """检查 MCP 服务器是否禁用"""
    manager = get_mcp_config_manager()
    return manager.is_server_disabled(name)


def set_mcp_server_enabled(name: str, enabled: bool):
    """设置 MCP 服务器启用/禁用"""
    manager = get_mcp_config_manager()
    manager.set_server_enabled(name, enabled)
