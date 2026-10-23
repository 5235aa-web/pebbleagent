"""Domain handlers for the JSON-RPC surface."""

from . import config, files, mcp, memory, misc, model, pairing, permission, project, session, usage
from .context import RpcContext

_DOMAINS = {
    "pairing": pairing.handle,
    "usage": usage.handle,
    "session": session.handle,
    "mcp": mcp.handle,
    "file": files.handle,
    "model": model.handle,
    "memory": memory.handle,
    "agent-memory": memory.handle,
    "permission": permission.handle,
    "path_whitelist": permission.handle,
    "project": project.handle,
    "config": config.handle,
    "pricing": config.handle,
}


def dispatch(ctx: RpcContext, method: str):
    """Route a method to its domain handler; unknown methods fall through to misc."""
    domain = method.split(".", 1)[0]
    handler = _DOMAINS.get(domain, misc.handle)
    return handler(ctx, method)
