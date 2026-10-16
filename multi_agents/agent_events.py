import logging
from enum import Enum
from typing import Any, Dict

logger = logging.getLogger(__name__)

class AgentEventType(Enum):
    TOOL_START = "tool_start"
    TOOL_COMPLETE = "tool_complete"
    MESSAGE = "message"

class AgentEventManager:
    def __init__(self):
        pass