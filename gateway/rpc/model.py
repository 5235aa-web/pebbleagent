"""Model provider and agent-model RPCs."""

import logging
import re
import uuid

from agent_core.llm_client import is_opencode_provider, provider_ready

logger = logging.getLogger(__name__)

_SHAPING_KEYS = ("extraHeaders", "sessionHeader", "reasoningMode", "maxEffort")
_BASE_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _ok(rid, **extra):
    return {"id": rid, "result": {"ok": True, **extra}}


def _err(rid, message):
    return {"id": rid, "result": {"ok": False, "error": message}}


def _models(provider) -> list[str]:
    raw = provider.get("models") if isinstance(provider, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return out


def _active_index(data, providers) -> int:
    try:
        idx = int(data.get("activeProviderIndex", 0))
    except (TypeError, ValueError):
        idx = 0
    if not (0 <= idx < len(providers)):
        return 0
    return idx


def _set_agent_model(srv, model: str) -> None:
    """Write the selected model without dropping other per-agent settings."""
    agent_cfg = srv._read_agent_model_config()
    agents = agent_cfg.setdefault("agents", {})
    entry = dict(agents.get(srv._current_agent) or {})
    entry["model_source"] = model
    entry["custom_model"] = None
    entry["temperature"] = None
    agents[srv._current_agent] = entry
    srv._write_agent_model_config(agent_cfg)


def _repair_agent_model(srv, providers, active_idx: int) -> int:
    """Keep the active provider, the agent model and the model owner in step.

    The active provider wins when it owns the configured model (resolving
    duplicate model ids). If another ready provider owns it, the active index
    follows that owner so the card on screen is the endpoint in use. Only when
    no ready provider owns the model does the active/first ready provider's
    first model replace it; with no usable provider the stale model is cleared.
    """
    agent_cfg = srv._read_agent_model_config()
    agents = agent_cfg.setdefault("agents", {})
    entry = dict(agents.get(srv._current_agent) or {})
    current = str(entry.get("model_source") or entry.get("custom_model") or "").strip()
    active = providers[active_idx] if 0 <= active_idx < len(providers) else None
    active_models = _models(active) if active else []

    if current:
        if provider_ready(active) and current in active_models:
            return active_idx
        for i, provider in enumerate(providers):
            if current in _models(provider) and provider_ready(provider):
                return i

    if provider_ready(active):
        new_model = active_models[0]
        if entry.get("model_source") != new_model or entry.get("custom_model"):
            entry["model_source"] = new_model
            entry["custom_model"] = None
            agents[srv._current_agent] = entry
            srv._write_agent_model_config(agent_cfg)
        return active_idx

    for i, provider in enumerate(providers):
        if provider_ready(provider):
            new_model = _models(provider)[0]
            if entry.get("model_source") != new_model or entry.get("custom_model"):
                entry["model_source"] = new_model
                entry["custom_model"] = None
                agents[srv._current_agent] = entry
                srv._write_agent_model_config(agent_cfg)
            return i

    # No usable provider at all: clear the stale model rather than letting the
    # chat send a model name that no endpoint owns.
    if current or entry.get("custom_model"):
        entry["model_source"] = ""
        entry["custom_model"] = None
        agents[srv._current_agent] = entry
        srv._write_agent_model_config(agent_cfg)
    return active_idx


def _parse_provider(srv, raw):
    """Validate and normalise one provider card. Returns (provider, error)."""
    if not isinstance(raw, dict):
        return None, "provider object required"

    name = str(raw.get("name") or "").strip()
    if not name:
        return None, "provider name required"

    base_url = str(raw.get("baseUrl") or "").strip()
    if base_url and not _BASE_URL_RE.match(base_url):
        return None, "base url must start with http:// or https://"

    models = _models({"models": raw.get("models")})
    for model in models:
        if any(ch.isspace() for ch in model):
            return None, f"model id must not contain spaces: {model!r} (use the provider's exact id)"

    api_key = str(raw.get("apiKey") or "").strip()
    headers = raw.get("extraHeaders")
    if headers is not None and not isinstance(headers, dict):
        return None, "extra headers must be a JSON object"
    return {
        "id": str(raw.get("slug") or "").strip() or str(uuid.uuid4()),
        "provider": name,
        "apiKey": api_key,
        "baseUrl": base_url,
        "models": models,
        "temperature": srv._num(raw.get("temperature")),
        "topP": srv._num(raw.get("topP")),
        "reasoning_effort": srv._str(raw.get("reasoning_effort")),
        "thinking_enabled": srv._bool(raw.get("thinking_enabled"), True),
    }, None


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "model.options":
        data = srv._read_providers()
        providers_raw = data.get("providers") or []
        if not isinstance(providers_raw, list):
            providers_raw = []
        active_idx = _active_index(data, providers_raw)
        active_provider = providers_raw[active_idx] if providers_raw else None

        agent_cfg = srv._read_agent_model_config().get("agents", {}).get(srv._current_agent, {})
        current_model = str(agent_cfg.get("custom_model") or agent_cfg.get("model_source") or "").strip()
        effective_idx = active_idx

        # Report the provider that will actually serve the model. The active
        # provider wins only when it owns the model (which resolves duplicate
        # ids); otherwise the model owner wins, preserving per-agent bindings
        # such as chat_agent -> deepseek-flash.
        active_models = _models(active_provider) if provider_ready(active_provider) else []
        if current_model and provider_ready(active_provider) and current_model in active_models:
            effective_idx = active_idx
        elif current_model:
            owner_idx = next(
                (i for i, p in enumerate(providers_raw)
                 if current_model in _models(p) and provider_ready(p)),
                None,
            )
            if owner_idx is not None:
                effective_idx = owner_idx
                active_provider = providers_raw[effective_idx]
            elif provider_ready(active_provider):
                effective_idx = active_idx
                current_model = active_models[0]
            else:
                ready_idx = next((i for i, p in enumerate(providers_raw) if provider_ready(p)), None)
                if ready_idx is not None:
                    effective_idx = ready_idx
                    active_provider = providers_raw[effective_idx]
                    current_model = _models(active_provider)[0]
                else:
                    current_model = ""
        else:
            if provider_ready(active_provider):
                effective_idx = active_idx
                current_model = active_models[0]
            else:
                ready_idx = next((i for i, p in enumerate(providers_raw) if provider_ready(p)), None)
                if ready_idx is not None:
                    effective_idx = ready_idx
                    active_provider = providers_raw[effective_idx]
                    current_model = _models(active_provider)[0]
                else:
                    current_model = ""

        providers_out = []
        for i, p in enumerate(providers_raw):
            opencode = is_opencode_provider(p.get("provider"))
            providers_out.append({
                "name": p.get("provider", f"Provider {i}"),
                "slug": p.get("id", f"p{i}"),
                "models": _models(p),
                "authenticated": bool(p.get("apiKey")),
                "is_current": i == effective_idx,
                "ready": provider_ready(p),
                "apiKey": p.get("apiKey", ""),
                "baseUrl": p.get("baseUrl", ""),
                "temperature": p.get("temperature"),
                "topP": p.get("topP"),
                "reasoning_effort": p.get("reasoning_effort"),
                "thinking_enabled": p.get("thinking_enabled") is not False,
                "extraHeaders": (p.get("extraHeaders") or {}) if opencode else {},
                "sessionHeader": (p.get("sessionHeader") or "") if opencode else "",
                "reasoningMode": (p.get("reasoningMode") or "thinking") if opencode else "thinking",
                "maxEffort": (p.get("maxEffort") or "downgrade") if opencode else "downgrade",
            })

        # Per-model pricing, so the picker can show cost inline instead of
        # forcing a second round trip (and a second source of truth). The table
        # lives in the data dir once the pricing pane has saved it, but a fresh
        # install only has the bundled copy, so both are read here. UsageTracker
        # uses the same pair, which keeps the label and the billing in step.
        pricing_out = {}
        currency = "CNY"
        try:
            import json
            pf = None
            for cand in (srv._DATA_DIR / "model_pricing.json", srv._PROJECT_ROOT / "model_pricing.json"):
                if cand.exists():
                    pf = cand
                    break
            if pf is not None:
                raw = json.loads(pf.read_text(encoding="utf-8"))
                currency = raw.get("default_currency") or "CNY"
                for name, entry in (raw.get("models") or {}).items():
                    p = entry.get("pricing") or {}
                    tier = entry.get("pricing_tier_high") or {}
                    row = {
                        "input": p.get("input_per_million"),
                        "cache_hit": p.get("cache_hit_per_million"),
                        "output": p.get("output_per_million"),
                    }
                    # Only models with a second tier carry a threshold; the pane
                    # needs it to tell "standard tier" from "high tier" instead
                    # of printing a word that never changes.
                    if tier:
                        row["tier_threshold"] = tier.get("threshold_tokens")
                    pricing_out[name] = row
        except Exception as e:
            logger.warning(f"[Server] Failed to read model pricing: {e}")

        return {
            "id": rid,
            "result": {
                "model": current_model or "",
                "provider": active_provider.get("provider", "") if active_provider else "",
                "providers": providers_out,
                "activeProviderIndex": effective_idx,
                "pricing": pricing_out,
                "currency": currency,
            }
        }

    if method == "model.provider.set":
        try:
            idx = int(params.get("index", 0))
        except (TypeError, ValueError):
            return _err(rid, "invalid provider index")
        data = srv._read_providers()
        providers = data.get("providers") or []
        if not (0 <= idx < len(providers)):
            return _err(rid, "provider index out of range")
        provider = providers[idx]
        if not provider_ready(provider):
            return _err(rid, "provider is not ready: add a base url and at least one model first")

        models = _models(provider)
        model = str(params.get("model") or "").strip()
        if model not in models:
            model = models[0]

        data["providers"] = providers
        data["activeProviderIndex"] = idx
        srv._write_providers(data)
        _set_agent_model(srv, model)
        return _ok(rid, index=idx, model=model)

    if method == "model.provider.save":
        try:
            idx = int(params.get("index", -1))
        except (TypeError, ValueError):
            return _err(rid, "invalid provider index")
        provider_data = params.get("provider", {})
        parsed, error = _parse_provider(srv, provider_data)
        if error:
            return _err(rid, error)

        data = srv._read_providers()
        providers = data.get("providers") or []
        if not isinstance(providers, list):
            providers = []
        existing = providers[idx] if 0 <= idx < len(providers) else {}

        # Merge instead of replacing: a hand-edited provider may carry fields
        # this panel does not know about, and a settings save must not erase them.
        merged = dict(existing)
        merged.update(parsed)
        if existing:
            merged["id"] = existing.get("id") or parsed["id"]

        # Request shaping is an OpenCode-only feature. Remove it when a card is
        # renamed to another provider so hidden values cannot keep affecting it.
        if is_opencode_provider(parsed["provider"]):
            for key in _SHAPING_KEYS:
                if key in provider_data:
                    value = provider_data.get(key)
                    if value:
                        merged[key] = value
                    else:
                        merged.pop(key, None)
                elif existing.get(key):
                    merged[key] = existing[key]
        else:
            for key in _SHAPING_KEYS:
                merged.pop(key, None)

        if 0 <= idx < len(providers):
            providers[idx] = merged
            saved_idx = idx
        else:
            providers.append(merged)
            saved_idx = len(providers) - 1

        active_idx = _active_index(data, providers)
        if idx < 0 and provider_ready(merged):
            active_idx = saved_idx

        data["providers"] = providers
        data["activeProviderIndex"] = active_idx
        srv._write_providers(data)

        repaired = _repair_agent_model(srv, providers, active_idx)
        if repaired != active_idx:
            data["activeProviderIndex"] = repaired
            srv._write_providers(data)

        return _ok(rid, index=saved_idx, id=merged.get("id", ""))

    if method == "model.provider.delete":
        try:
            idx = int(params.get("index", -1))
        except (TypeError, ValueError):
            return _err(rid, "invalid provider index")
        data = srv._read_providers()
        providers = data.get("providers") or []
        if not (0 <= idx < len(providers)):
            return _err(rid, "provider index out of range")

        active_idx = _active_index(data, providers)
        providers.pop(idx)
        if idx < active_idx:
            active_idx -= 1
        elif idx == active_idx:
            active_idx = min(idx, len(providers) - 1) if providers else 0
        if not (0 <= active_idx < len(providers)):
            active_idx = 0

        data["providers"] = providers
        data["activeProviderIndex"] = active_idx
        srv._write_providers(data)

        repaired = _repair_agent_model(srv, providers, active_idx)
        if repaired != active_idx:
            data["activeProviderIndex"] = repaired
            srv._write_providers(data)
        return _ok(rid, index=repaired)

    if method == "model.params.set":
        temperature = params.get("temperature")
        top_p = params.get("topP")
        reasoning_effort = params.get("reasoning_effort")
        thinking_enabled = srv._bool(params.get("thinking_enabled"))
        data = srv._read_providers()
        providers = data.get("providers") or []
        if "index" in params and params.get("index") is not None:
            try:
                idx = int(params.get("index"))
            except (TypeError, ValueError):
                return _err(rid, "invalid provider index")
        else:
            idx = _active_index(data, providers)
        if not (0 <= idx < len(providers)):
            return _err(rid, "provider index out of range")

        provider = providers[idx]
        if temperature is not None:
            provider["temperature"] = temperature
        if top_p is not None:
            provider["topP"] = top_p
        if reasoning_effort is not None:
            provider["reasoning_effort"] = reasoning_effort
        if thinking_enabled is not None:
            provider["thinking_enabled"] = thinking_enabled
        data["providers"] = providers
        srv._write_providers(data)
        return _ok(rid, index=idx)

    if method == "model.switch":
        model = str(params.get("model") or "").strip()
        data = srv._read_providers()
        providers = data.get("providers") or []
        idx = _active_index(data, providers)
        if not (0 <= idx < len(providers)):
            return _err(rid, "no active provider")
        provider = providers[idx]
        target_idx = None
        if provider_ready(provider) and model in _models(provider):
            target_idx = idx
        else:
            # The same model may belong to another provider (per-agent binding
            # or a duplicate id). Move the active index to the owner so the
            # endpoint, the card and the next request agree.
            target_idx = next(
                (i for i, p in enumerate(providers)
                 if model in _models(p) and provider_ready(p)),
                None,
            )
        if target_idx is None:
            return _err(rid, f"model {model!r} does not belong to any ready provider")
        if target_idx != idx:
            data["activeProviderIndex"] = target_idx
            srv._write_providers(data)
        _set_agent_model(srv, model)
        return _ok(rid, model=model, index=target_idx)

    return None
