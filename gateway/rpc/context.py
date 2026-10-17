"""Dispatch context shared by RPC domain handlers."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class RpcContext:
    """Everything a domain handler needs from the gateway module."""

    srv: Any
    rid: str
    params: Dict[str, Any]
    llm_config: Dict[str, Any]
    active_model: str
