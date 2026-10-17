"""Experience ledger and agent-note RPCs."""

import logging

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "memory.list":
        kind = params.get("kind") or params.get("memory_type") or None
        status = params.get("status", "active")
        try:
            entries = srv._ledger().list(kind=kind, status=status if status != "all" else None)
            result = [srv._serialize_experience(e) for e in entries]
            return {"id": rid, "result": {"success": True, "memories": result, "count": len(result)}}
        except Exception as e:
            logger.error(f"[Server] memory.list failed: {e}")
            return {"id": rid, "result": {"success": False, "memories": [], "count": 0, "error": str(e)}}

    if method == "memory.stats":
        try:
            return {"id": rid, "result": {"success": True, **srv._ledger().stats()}}
        except Exception as e:
            logger.error(f"[Server] memory.stats failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "memory.get":
        exp_id = params.get("id", "")
        if not exp_id:
            return {"id": rid, "error": {"code": -32000, "message": "id required"}}
        try:
            entry = srv._ledger().get(exp_id)
            if entry:
                return {"id": rid, "result": {"success": True, "memory": srv._serialize_experience(entry)}}
            return {"id": rid, "result": {"success": False, "memory": None, "error": "not found"}}
        except Exception as e:
            logger.error(f"[Server] memory.get failed: {e}")
            return {"id": rid, "result": {"success": False, "memory": None, "error": str(e)}}

    if method == "memory.create":
        kind = params.get("kind", "preference")
        statement = (params.get("statement") or "").strip()
        rationale = params.get("rationale", "")
        applies_to = params.get("applies_to") or {}
        confidence = srv._num(params.get("confidence"))
        if not statement:
            return {"id": rid, "error": {"code": -32000, "message": "statement required"}}
        try:
            entry = srv._ledger().create(
                kind=kind,
                statement=statement,
                rationale=rationale,
                applies_to=applies_to if isinstance(applies_to, dict) else {},
                confidence=confidence if confidence is not None else 0.8,
            )
            if not entry:
                return {"id": rid, "result": {"success": False, "memory": None, "error": "invalid entry"}}
            return {"id": rid, "result": {"success": True, "memory": srv._serialize_experience(entry)}}
        except Exception as e:
            logger.error(f"[Server] memory.create failed: {e}")
            return {"id": rid, "result": {"success": False, "memory": None, "error": str(e)}}

    if method == "memory.update":
        exp_id = params.get("id", "")
        if not exp_id:
            return {"id": rid, "error": {"code": -32000, "message": "id required"}}
        fields = {}
        for key in ("statement", "rationale", "kind", "applies_to", "status"):
            if params.get(key) is not None:
                fields[key] = params[key]
        if params.get("confidence") is not None:
            fields["confidence"] = srv._num(params.get("confidence"))
        try:
            entry = srv._ledger().update(exp_id, **fields)
            if entry:
                return {"id": rid, "result": {"success": True, "memory": srv._serialize_experience(entry)}}
            return {"id": rid, "result": {"success": False, "memory": None, "error": "not found"}}
        except Exception as e:
            logger.error(f"[Server] memory.update failed: {e}")
            return {"id": rid, "result": {"success": False, "memory": None, "error": str(e)}}

    if method == "memory.delete":
        exp_id = params.get("id", "")
        if not exp_id:
            return {"id": rid, "error": {"code": -32000, "message": "id required"}}
        try:
            return {"id": rid, "result": {"success": srv._ledger().delete(exp_id)}}
        except Exception as e:
            logger.error(f"[Server] memory.delete failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "memory.prompt":
        agent_name = params.get("agent_name", None)
        query = params.get("query", "")
        try:
            prompt = srv._ledger().build_prompt(query=query, agent_type=agent_name)
            return {"id": rid, "result": {
            "success": True,
            "prompt": prompt,
            "combined_prompt": prompt,
            }}
        except Exception as e:
            logger.error(f"[Server] memory.prompt failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "agent-memory.get":
        agent_type = params.get("agent_type", "")
        scope = params.get("scope", "project")
        if not agent_type:
            return {"id": rid, "error": {"code": -32000, "message": "agent_type required"}}
        try:
            content = srv._ledger().get_agent_note(agent_type, scope)
            return {"id": rid, "result": {"success": True, "agent_type": agent_type, "scope": scope, "content": content, "exists": bool(content)}}
        except Exception as e:
            logger.error(f"[Server] agent-memory.get failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "agent-memory.set":
        agent_type = params.get("agent_type", "")
        scope = params.get("scope", "project")
        content = params.get("content", "")
        if not agent_type:
            return {"id": rid, "error": {"code": -32000, "message": "agent_type required"}}
        try:
            return {"id": rid, "result": {"success": srv._ledger().set_agent_note(agent_type, scope, content)}}
        except Exception as e:
            logger.error(f"[Server] agent-memory.set failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "agent-memory.clear":
        agent_type = params.get("agent_type", "")
        scope = params.get("scope", "project")
        if not agent_type:
            return {"id": rid, "error": {"code": -32000, "message": "agent_type required"}}
        try:
            return {"id": rid, "result": {"success": srv._ledger().clear_agent_note(agent_type, scope)}}
        except Exception as e:
            logger.error(f"[Server] agent-memory.clear failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    return None
