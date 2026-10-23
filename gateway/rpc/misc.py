"""Agent, note, message, export, and completion RPCs."""

import logging

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "theme.get":
        theme = srv._load_theme()
        return {"id": rid, "result": {"success": True, "theme": theme}}

    if method == "theme.save":
        data = params.get("theme") or {}
        if not isinstance(data, dict):
            return {"id": rid, "error": {"code": -32000, "message": "theme object required"}}
        ok = srv._save_theme(data)
        return {"id": rid, "result": {"success": ok}}

    if method == "message.delete":
        """删除单条消息（同步删除 sessions.db 和 agent_chat.db）"""
        sid = params.get("session_id", "")
        message_id = params.get("message_id", 0)
        if not sid or not message_id:
            return {"id": rid, "error": {"code": -32000, "message": "session_id and message_id required"}}
        result = srv._session_store.delete_message(sid, message_id)
        deleted = result.get("deleted", False)
        msg_uuid = result.get("msg_uuid", "")
        # 同步删除 agent_chat.db
        if deleted and msg_uuid:
            try:
                from agent_core.agent_chat_history import delete_agent_chat_message_by_uuid
                delete_agent_chat_message_by_uuid(msg_uuid)
            except Exception as e:
                logger.warning(f"[Server] Failed to delete agent_chat message by uuid {msg_uuid}: {e}")
        return {"id": rid, "result": {"deleted": deleted}}

    if method == "setup.status":
        ctx.llm_config = srv.load_llm_config(srv._current_agent)
        configured = bool(ctx.llm_config.get("api_key"))
        return {"id": rid, "result": {"provider_configured": configured}}

    if method == "complete.slash":
        items = [
        {"display": "/agent [名称]", "text": "/agent ", "meta": "切换或选择Agent"},
        {"display": "/help", "text": "/help", "meta": "显示可用命令"},
        {"display": "/mcp", "text": "/mcp", "meta": "管理MCP服务器"},
        {"display": "/mcps", "text": "/mcps", "meta": "管理MCP服务器"},
        {"display": "/model", "text": "/model ", "meta": "LLM配置(供应商/模型/参数)"},
        {"display": "/new", "text": "/new", "meta": "新建会话"},
        {"display": "/quit", "text": "/quit", "meta": "退出pebble"},
        {"display": "/redo", "text": "/redo", "meta": "重试上一条助手回复"},
        {"display": "/session delete", "text": "/session delete", "meta": "删除当前会话"},
        {"display": "/session", "text": "/session ", "meta": "切换/管理会话"},
        {"display": "/whitelist", "text": "/whitelist", "meta": "管理路径白名单"},
        {"display": "/workdir [path]", "text": "/workdir ", "meta": "更改工作区目录"},
        ]
        return {
        "id": rid,
        "result": {
        "items": items,
        "replace_from": 1,
        }
        }

    if method == "slash.exec":
        return {"id": rid, "result": {"output": "ok"}}

    if method == "command.dispatch":
        name = params.get("name", "")
        if name == "model":
            return {"id": rid, "result": {"type": "exec", "output": "use /model to open config panel"}}
        return {"id": rid, "result": {"type": "exec", "output": f"unknown command: {name}"}}

    if method == "agent.list":
        agents = []
        try:
            raw = srv.list_available_agents_from_yaml()
            for agent in raw:
                agents.append({
                "name": agent.get("name", ""),
                "description": agent.get("description", ""),
                "current": agent.get("name", "") == srv._current_agent,
                })
        except Exception as e:
            logger.warning(f"[Server] Failed to list agents: {e}")
            agents = [{"name": "code_agent", "description": "Code expert", "current": True}]
        return {"id": rid, "result": {"agents": agents}}

    if method == "agent.switch":
        name = params.get("name", "")
        available = []
        try:
            for a in srv.list_available_agents_from_yaml():
                available.append(a.get("name", ""))
        except:
            pass
        if name not in available:
            return {"id": rid, "result": {"ok": False, "error": f"Unknown agent: {name}", "available": available}}
        srv._current_agent = name
        logger.info(f"[Server] Agent switched to: {name}")
        # Reload LLM config for the new agent
        ctx.llm_config = srv.load_llm_config(srv._current_agent)
        # The other client shows the agent in its header; let it refresh.
        srv.broadcast_event("agent.changed", {"name": name})
        return {"id": rid, "result": {"ok": True, "agent": name}}

    if method == "clarify.respond":
        answer = params.get("answer", "")
        request_id = params.get("request_id", "")
        answers = params.get("answers") or None  # 新：多问题结构化答案 {问题文本: 答案}
        srv._submit_clarify_answer(answer, request_id, answers)
        return {"id": rid, "result": {"ok": True}}

    if method == "clarify.list":
        """The pending question, so a reconnecting client does not lose it.

        QuestionManager is a process-wide singleton and clears its state on
        submit, so a client that comes back after a dropped socket has nothing
        local to rebuild the card from.
        """
        try:
            from agent_core.state.question import QuestionManager
            pending = QuestionManager.get_pending_questions()
            if not pending:
                return {"id": rid, "result": {"questions": None, "session_id": ""}}
            return {"id": rid, "result": {
                "questions": pending,
                "session_id": QuestionManager.get_pending_session_id(),
            }}
        except Exception as e:
            logger.warning(f"[Server] clarify.list error: {e}")
            return {"id": rid, "result": {"questions": None, "session_id": ""}}

    if method == "usernote.read":
        return {"id": rid, "result": {"content": srv._load_usernote()}}

    if method == "usernote.write":
        content = params.get("content", "")
        srv._save_usernote(content)
        return {"id": rid, "result": {"success": True, "content": content}}

    if method == "quick-notes.read":
        return {"id": rid, "result": {"notes": srv._load_quick_notes()}}

    if method == "quick-notes.write":
        notes = params.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        srv._save_quick_notes(notes)
        return {"id": rid, "result": {"success": True, "notes": notes}}

    # ==================== 文件变更面板 (File Changes) ====================

    return None
