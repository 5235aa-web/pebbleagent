"""Per-provider HTTP headers and reasoning-parameter shaping.

Some OpenAI-compatible gateways need extra headers on every request (OpenCode Go
rejects a request without its session header) and accept only the standard
`reasoning_effort` field rather than a vendor-specific `thinking` block. Both
behaviours are opt-in through provider config, so providers that do not set them
keep the previous request shape exactly.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def is_opencode_provider(name: Any) -> bool:
    """True for the OpenCode family (`opencode`, `OpenCode Go`, `opencode-go`)."""
    normalized = str(name or "").lower().replace(" ", "").replace("_", "").replace("-", "")
    return normalized in ("opencode", "opencodego")


def provider_ready(provider: Optional[Dict[str, Any]]) -> bool:
    """A provider can serve chat only with a base URL and at least one model.

    The API key is deliberately not required: local OpenAI-compatible servers
    often accept any placeholder. The UI shows a separate "no key" warning.
    """
    if not isinstance(provider, dict):
        return False
    entry = provider
    models = entry.get("models") or []
    if isinstance(models, str):
        models = [models]
    has_model = isinstance(models, (list, tuple)) and any(str(m).strip() for m in models)
    return bool(str(entry.get("baseUrl") or "").strip()) and has_model


def shaping_from_provider(provider: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract the optional request-shaping fields from a raw provider entry.

    Shared by the gateway config loader (selects the active provider) and the
    agent-side provider resolution (selects the provider that owns the model), so
    both paths hand the same values to the client and the request builder.

    The block is OpenCode-only: a hand-edited `extraHeaders` on another provider
    must not silently change its request shape while the settings panel hides
    the controls.
    """
    entry = provider if isinstance(provider, dict) else {}
    defaults = {
        "extra_headers": {},
        "session_header": "",
        "reasoning_mode": "thinking",
        "max_effort": "downgrade",
    }
    if not is_opencode_provider(entry.get("provider")):
        return defaults

    raw_headers = entry.get("extraHeaders")
    extra_headers = (
        {str(k): str(v) for k, v in raw_headers.items() if k and v is not None}
        if isinstance(raw_headers, dict)
        else {}
    )

    raw_session = entry.get("sessionHeader")
    session_header = raw_session if isinstance(raw_session, str) else ""

    raw_mode = entry.get("reasoningMode")
    reasoning_mode = raw_mode if raw_mode in ("thinking", "effort_only") else "thinking"

    raw_effort = entry.get("maxEffort")
    max_effort = raw_effort if raw_effort in ("keep", "downgrade") else "downgrade"

    return {
        "extra_headers": extra_headers,
        "session_header": session_header,
        "reasoning_mode": reasoning_mode,
        "max_effort": max_effort,
    }


def normalize_base_url(raw: str) -> str:
    """Return the API root an OpenAI-compatible client expects.

    Users paste the full chat URL or the Responses endpoint often enough that
    appending `/chat/completions` to it produced paths such as
    `.../api/v1/responses/chat/completions` and a 404. Both suffixes are
    stripped here so every caller builds the request from the same root.
    """
    url = (raw or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


def build_default_headers(
    llm_config: Optional[Dict[str, Any]], session_id: str = ""
) -> Optional[Dict[str, str]]:
    """Headers for the OpenAI client, or None when the provider configures none.

    `extra_headers` is copied verbatim. `session_header` names a header whose
    value is the conversation id, which gateways use for routing and prompt
    caching.
    """
    config = llm_config or {}
    headers: Dict[str, str] = {}

    extra = config.get("extra_headers")
    if isinstance(extra, dict):
        for key, value in extra.items():
            if isinstance(key, str) and key and value is not None:
                headers[key] = str(value)

    name = config.get("session_header")
    if isinstance(name, str) and name and session_id:
        headers[name] = str(session_id)

    return headers or None


def keep_max_effort(llm_config: Optional[Dict[str, Any]]) -> bool:
    """True when this provider accepts the "max" effort tier for non-DeepSeek models.

    The default downgrade exists because some vendors reject "max" with a 400;
    gateways that do accept it opt in with `maxEffort: "keep"`.
    """
    return ((llm_config or {}).get("max_effort") or "downgrade") == "keep"


def apply_reasoning_mode(
    params: Dict[str, Any], llm_config: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Strip the vendor-specific `thinking` block in "effort_only" mode.

    The default mode ("thinking") leaves the request untouched, which is what
    DeepSeek/MiMo-style endpoints expect. "effort_only" matches how the OpenCode
    CLI talks to openai-compatible gateways: send `reasoning_effort` only.
    """
    config = llm_config or {}
    if (config.get("reasoning_mode") or "thinking") != "effort_only":
        return params

    extra = params.get("extra_body")
    if isinstance(extra, dict):
        extra.pop("thinking", None)
        if not extra:
            params.pop("extra_body", None)
    return params
