import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from agent_core.llm_client import normalize_base_url, provider_ready, shaping_from_provider

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(Path.home(), ".pebble")
_DATA_DIR = Path(_DATA_HOME)


def _try_read_json(*candidates: Path) -> Optional[dict]:
    """Read the first existing JSON file without silently hiding corruption.

    A missing user file may fall back to the bundled copy. An existing but
    unreadable/corrupt user file must not: falling back to bundled sample
    providers would make the panel look empty/defaulted and a later save could
    overwrite the real file with those samples.
    """
    for p in candidates:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Config] Failed to read {p}: {e}")
            return None
    return None


def _provider_for_model(providers: list, model: str) -> Optional[dict]:
    """The provider that owns `model`, matching the agent-side resolver."""
    if not model:
        return None
    for candidate in providers:
        if model in _models(candidate):
            return candidate
    return None


def _models(provider: dict) -> list[str]:
    raw = provider.get("models") if isinstance(provider, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(m).strip() for m in raw if str(m).strip()]


def _active_index(data: dict, providers: list) -> int:
    try:
        idx = int(data.get("activeProviderIndex", 0))
    except (TypeError, ValueError):
        idx = 0
    return idx if 0 <= idx < len(providers) else 0


def load_llm_config(agent_name: str = "code_agent") -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "")
    # Optional per-provider request shaping; empty defaults mean "behave as before".
    extra_headers: Dict[str, str] = {}
    session_header = ""
    reasoning_mode = "thinking"
    max_effort = "downgrade"

    # 从 llm_providers.json 读取配置（优先用户数据目录，回退到打包根）
    data = _try_read_json(
        _DATA_DIR / "llm_providers.json",
        _PROJECT_ROOT / "llm_providers.json",
    )
    if data:
        try:
            providers = data.get("providers") or []
            if isinstance(providers, list) and providers:
                active_idx = _active_index(data, providers)
                active_provider = providers[active_idx]
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
                chosen_model = custom_model or model

                # Prefer the active provider only when it owns the agent's
                # model. A different provider that owns the model wins, which
                # preserves per-agent model bindings (chat_agent -> deepseek)
                # while still resolving duplicate model ids to the active card.
                provider = None
                active_models = _models(active_provider) if provider_ready(active_provider) else []
                if chosen_model and chosen_model in active_models:
                    provider = active_provider
                elif chosen_model:
                    owner = _provider_for_model(providers, chosen_model)
                    if owner is not None and provider_ready(owner):
                        provider = owner
                if provider is None:
                    if provider_ready(active_provider):
                        provider = active_provider
                    else:
                        provider = next((p for p in providers if provider_ready(p)), None)
                if provider is None:
                    provider = active_provider or providers[0]

                if provider:
                    provider_models = _models(provider)
                    if provider_ready(provider):
                        model = chosen_model if chosen_model in provider_models else provider_models[0]
                    elif chosen_model and chosen_model in provider_models:
                        model = chosen_model
                    elif provider_models:
                        model = provider_models[0]
                    api_key = provider.get("apiKey") or api_key
                    base_url = normalize_base_url(provider.get("baseUrl") or base_url)
                    shaping = shaping_from_provider(provider)
                    extra_headers = shaping["extra_headers"]
                    session_header = shaping["session_header"]
                    reasoning_mode = shaping["reasoning_mode"]
                    max_effort = shaping["max_effort"]
        except Exception as e:
            logger.warning(f"[Config] Failed to load llm_providers.json: {e}")

    return {
        "api_key": api_key or "local-dev-key",
        "base_url": base_url or "http://localhost:8080/v1",
        "model": model or "gpt-4",
        "extra_headers": extra_headers,
        "session_header": session_header,
        "reasoning_mode": reasoning_mode,
        "max_effort": max_effort,
    }


def load_tools_info() -> Dict[str, list]:
    tools: Dict[str, list] = {"Tools": []}
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        from agent_core.tool_definitions import get_tools_definition
        for tool in get_tools_definition():
            name = tool.get("function", {}).get("name", "")
            if name:
                tools["Tools"].append(name)
    except Exception as e:
        logger.warning(f"[Config] Failed to load tool definitions: {e}")
    return tools


def load_skills_info() -> Dict[str, list]:
    """Installed skills, grouped by where they come from.

    Both roots collapse into one "Skills" heading: the panel is a catalogue of
    what the agent can load, and splitting it by install location made the
    section read as a second category beside Tools. A user skill shadows a
    built-in of the same name, so it is listed once.
    """
    skills: Dict[str, list] = {}
    roots = [(_DATA_DIR / "skills", "Skills"), (_PROJECT_ROOT / "skills", "Skills")]
    seen: set = set()
    for root, category in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name in seen or not (entry / "SKILL.md").exists():
                continue
            seen.add(entry.name)
            skills.setdefault(category, []).append(entry.name)
    return skills
