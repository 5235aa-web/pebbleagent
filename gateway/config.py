import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(Path.home(), ".pebble")
_DATA_DIR = Path(_DATA_HOME)


def _try_read_json(*candidates: Path) -> Optional[dict]:
    """读取 JSON 文件，按顺序尝试多个候选路径，返回第一个存在的"""
    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def load_llm_config(agent_name: str = "code_agent") -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "")

    # 从 llm_providers.json 读取配置（优先用户数据目录，回退到打包根）
    data = _try_read_json(
        _DATA_DIR / "llm_providers.json",
        _PROJECT_ROOT / "llm_providers.json",
    )
    if data:
        try:
            providers = data.get("providers", [])
            active_idx = data.get("activeProviderIndex", 0)
            if providers and 0 <= active_idx < len(providers):
                provider = providers[active_idx]
                api_key = provider.get("apiKey", api_key)
                raw_url = provider.get("baseUrl", base_url)
                if raw_url.endswith("/chat/completions"):
                    raw_url = raw_url.rsplit("/chat/completions", 1)[0]
                base_url = raw_url
                # 检查 agent_model_config.json 是否有自定义模型
                agent_cfg = _try_read_json(
                    _DATA_DIR / "agent_model_config.json",
                    _PROJECT_ROOT / "agent_model_config.json",
                )
                custom_model = None
                if agent_cfg:
                    try:
                        agent_entry = agent_cfg.get("agents", {}).get(agent_name, {})
                        src = agent_entry.get("model_source", "")
                        if src and src not in ("inherit", "custom", ""):
                            custom_model = src
                    except Exception:
                        pass
                fallback_models = provider.get("models", [])
                if custom_model:
                    model = custom_model
                elif model:
                    pass  # 环境变量中指定了
                elif fallback_models:
                    model = fallback_models[0]
        except Exception as e:
            logger.warning(f"[Config] Failed to load llm_providers.json: {e}")

    return {
        "api_key": api_key or "local-dev-key",
        "base_url": base_url or "http://localhost:8080/v1",
        "model": model or "gpt-4",
    }


def load_tools_info() -> Dict[str, list]:
    tools: Dict[str, list] = {"Agent": []}
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from agent_core.tool_definitions import get_tools_definition
        for tool in get_tools_definition():
            name = tool.get("function", {}).get("name", "")
            if name:
                tools["Agent"].append(name)
    except Exception as e:
        logger.warning(f"[Config] Failed to load tool definitions: {e}")
    return tools


def load_skills_info() -> Dict[str, list]:
    skills: Dict[str, list] = {}
    skills_dir = _PROJECT_ROOT / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and (entry / "SKILL.md").exists():
                category = "Built-in"
                skills.setdefault(category, []).append(entry.name)
    return skills
