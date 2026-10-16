import logging

logger = logging.getLogger(__name__)

def get_agent(name: str):
    logger.warning(f"get_agent called for {name}")
    return None

def set_parent_config(config: dict):
    pass