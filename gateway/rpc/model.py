"""Model provider and agent-model RPCs."""

import logging
import uuid

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "model.options":
        data = srv._read_providers()
        providers_raw = data.get("providers", [])
        active_idx = data.get("activeProviderIndex", 0)
        active_provider = providers_raw[active_idx] if providers_raw and active_idx < len(providers_raw) else None
        providers_out = []
        for i, p in enumerate(providers_raw):
            providers_out.append({
            "name": p.get("provider", f"Provider {i}"),
            "slug": p.get("id", f"p{i}"),
            "models": p.get("models", []),
            "authenticated": bool(p.get("apiKey")),
            "is_current": i == active_idx,
            "apiKey": p.get("apiKey", ""),
            "baseUrl": p.get("baseUrl", ""),
            "temperature": p.get("temperature"),
            "topP": p.get("topP"),
            "reasoning_effort": p.get("reasoning_effort"),
            "thinking_enabled": p.get("thinking_enabled") is not False,
            "max_tokens": p.get("maxTokens", 4096),
            "extraHeaders": p.get("extraHeaders") or {},
            "sessionHeader": p.get("sessionHeader") or "",
            "reasoningMode": p.get("reasoningMode") or "thinking",
            "maxEffort": p.get("maxEffort") or "downgrade",
            })
        current_model = active_provider.get("models", [None])[0] if active_provider else None
        agent_cfg = srv._read_agent_model_config().get("agents", {}).get(srv._current_agent, {})
        custom_model = agent_cfg.get("custom_model") or agent_cfg.get("model_source")
        if custom_model and custom_model not in ("inherit", "custom"):
            current_model = custom_model
        return {
        "id": rid,
        "result": {
        "model": current_model or "",
        "provider": active_provider.get("provider", "") if active_provider else "",
        "providers": providers_out,
        "activeProviderIndex": active_idx,
        }
        }

    if method == "model.provider.set":
        idx = params.get("index", 0)
        model = params.get("model", "")
        data = srv._read_providers()
        providers = data.get("providers", [])
        if 0 <= idx < len(providers):
            data["activeProviderIndex"] = idx
            srv._write_providers(data)
            if model and model in providers[idx].get("models", []):
                agent_cfg = srv._read_agent_model_config()
                agent_cfg.setdefault("agents", {})[srv._current_agent] = {
                "model_source": model,
                "custom_model": None,
                "temperature": None,
                "max_tokens": None,
                }
                srv._write_agent_model_config(agent_cfg)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.provider.save":
        idx = params.get("index", -1)
        provider_data = params.get("provider", {})
        data = srv._read_providers()
        providers = data.get("providers", [])
        new_provider = {
        "id": provider_data.get("slug", str(uuid.uuid4())),
        "provider": provider_data.get("name", "New Provider"),
        "apiKey": provider_data.get("apiKey", ""),
        "baseUrl": provider_data.get("baseUrl", ""),
        "models": provider_data.get("models", []),
        "temperature": srv._num(provider_data.get("temperature")),
        "maxTokens": srv._num(provider_data.get("maxTokens")) or 4096,
        "topP": srv._num(provider_data.get("topP")),
        "reasoning_effort": srv._str(provider_data.get("reasoning_effort")),
        "thinking_enabled": srv._bool(provider_data.get("thinking_enabled"), True),
        }
        # Request-shaping fields are edited by hand in llm_providers.json; the
        # settings UI does not carry them, so keep the stored values on save
        # instead of dropping them.
        existing = providers[idx] if 0 <= idx < len(providers) else {}
        for key in ("extraHeaders", "sessionHeader", "reasoningMode", "maxEffort"):
            value = provider_data.get(key, existing.get(key))
            if value:
                new_provider[key] = value
        if idx >= 0 and idx < len(providers):
            providers[idx] = new_provider
        else:
            providers.append(new_provider)
            data["activeProviderIndex"] = len(providers) - 1
        data["providers"] = providers
        srv._write_providers(data)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.provider.delete":
        idx = params.get("index", -1)
        data = srv._read_providers()
        providers = data.get("providers", [])
        if 0 <= idx < len(providers):
            providers.pop(idx)
            if data.get("activeProviderIndex", 0) >= len(providers):
                data["activeProviderIndex"] = max(0, len(providers) - 1)
            data["providers"] = providers
            srv._write_providers(data)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.params.set":
        temperature = params.get("temperature")
        top_p = params.get("topP")
        reasoning_effort = params.get("reasoning_effort")
        thinking_enabled = srv._bool(params.get("thinking_enabled"))
        data = srv._read_providers()
        idx = data.get("activeProviderIndex", 0)
        providers = data.get("providers", [])
        if 0 <= idx < len(providers):
            if temperature is not None:
                providers[idx]["temperature"] = temperature
            if top_p is not None:
                providers[idx]["topP"] = top_p
            if reasoning_effort is not None:
                providers[idx]["reasoning_effort"] = reasoning_effort
            if thinking_enabled is not None:
                providers[idx]["thinking_enabled"] = thinking_enabled
            data["providers"] = providers
            srv._write_providers(data)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.switch":
        model = params.get("model", "")
        data = srv._read_providers()
        providers = data.get("providers", [])
        idx = data.get("activeProviderIndex", 0)
        if 0 <= idx < len(providers) and model in providers[idx].get("models", []):
            agent_cfg = srv._read_agent_model_config()
            agent_cfg.setdefault("agents", {})[srv._current_agent] = {
            "model_source": model,
            "custom_model": None,
            "temperature": None,
            "max_tokens": None,
            }
            srv._write_agent_model_config(agent_cfg)
        return {"id": rid, "result": {"ok": True}}

    return None
