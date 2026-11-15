"""Agent, note, message, export, and completion RPCs."""

import logging
import os
import sys
import uuid

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

    if method == "message.forward":
        """同一项目下将源会话选中的多条消息合并打包转发到目标会话。"""
        source_sid = str(params.get("source_session_id") or "").strip()
        target_sid = str(params.get("target_session_id") or "").strip()
        message_ids = params.get("message_ids")
        if not source_sid or not target_sid or not isinstance(message_ids, list) or not message_ids:
            return {"id": rid, "error": {"code": -32000, "message": "source_session_id, target_session_id and message_ids required"}}
        if source_sid == target_sid:
            return {"id": rid, "error": {"code": -32000, "message": "cannot forward messages to the same session"}}

        src_session = srv._session_store.get(source_sid)
        dst_session = srv._session_store.get(target_sid)
        if not src_session:
            return {"id": rid, "error": {"code": -32000, "message": f"Source session not found: {source_sid}"}}
        if not dst_session:
            return {"id": rid, "error": {"code": -32000, "message": f"Target session not found: {target_sid}"}}

        src_proj = srv._norm_project_path(src_session.get("project_path") or "")
        dst_proj = srv._norm_project_path(dst_session.get("project_path") or "")
        if src_proj.lower() != dst_proj.lower():
            return {"id": rid, "error": {"code": -32000, "message": "can only forward messages within the same project"}}

        if srv.is_session_running(target_sid):
            return {"id": rid, "error": {"code": -32000, "message": "target session is currently running; wait until it finishes"}}

        rows, tool_outputs = srv._session_store.get_messages_by_ids(source_sid, message_ids)
        if not rows:
            return {"id": rid, "error": {"code": -32000, "message": "no matching messages found in source session"}}

        from gateway.forward_formatter import format_forwarded_messages

        merged_text, used_count = format_forwarded_messages(
            src_session.get("title") or "",
            rows,
            tool_outputs=tool_outputs,
        )
        if not merged_text or used_count == 0:
            return {"id": rid, "error": {"code": -32000, "message": "selected messages contain no forwardable content"}}

        if bool(params.get("prepare_only")):
            logger.info(
                f"[Forward] Prepared {used_count} message(s) from {source_sid[:8]} -> {target_sid[:8]} "
                f"({len(merged_text)} chars)"
            )
            return {
                "id": rid,
                "result": {
                    "ok": True,
                    "target_session_id": target_sid,
                    "forwarded_count": used_count,
                    "content": merged_text,
                },
            }

        new_msg_uuid = str(uuid.uuid4())
        preview_hint = f"[Forward] From \"{src_session.get('title') or 'Untitled session'}\" ({used_count} msgs)"
        new_msg_id, _ = srv._session_store.add_forwarded_user_message(
            target_sid,
            merged_text,
            msg_uuid=new_msg_uuid,
            preview=preview_hint,
        )

        # 同步写入 agent_chat.db，保证目标会话后续 LLM 对话可读取转发内容
        try:
            from agent_core import agent_chat_history as history
            try:
                from agent_core.context_compressor import estimate_tokens
                tok_count = estimate_tokens(merged_text)
            except Exception:
                tok_count = max(1, len(merged_text) // 4)
            agent_name = getattr(srv, "_current_agent", "code_agent") or "code_agent"
            conv = history.get_agent_conversation_by_uuid(target_sid)
            if not conv:
                history.create_agent_conversation(
                    "default_user",
                    agent_name,
                    title=dst_session.get("title") or "New conversation",
                    uuid=target_sid,
                )
                conv = history.get_agent_conversation_by_uuid(target_sid)
            if conv:
                history.add_agent_chat_message(
                    conversation_id=conv["id"],
                    role="user",
                    content=merged_text,
                    token_count=tok_count,
                    agent_name=agent_name,
                    msg_uuid=new_msg_uuid,
                )
        except Exception as e:
            logger.warning(f"[Server] Failed to sync forwarded message to agent_chat.db for {target_sid}: {e}")

        updated_dst = srv._session_store.get(target_sid) or {}
        srv.broadcast_event("message.forwarded", {
            "source_session_id": source_sid,
            "target_session_id": target_sid,
            "message_id": new_msg_id,
            "forwarded_count": used_count,
            "message_count": updated_dst.get("message_count", 0),
        })
        return {
            "id": rid,
            "result": {
                "ok": True,
                "target_session_id": target_sid,
                "message_id": new_msg_id,
                "forwarded_count": used_count,
            },
        }

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
        try:
            cfg = srv._read_agent_model_config()
            cfg["current_agent"] = name
            srv._write_agent_model_config(cfg)
        except Exception as e:
            logger.warning(f"[Server] Failed to persist agent selection: {e}")
        # Align the provider card with the new agent's model owner. Without this
        # the file's activeProviderIndex can keep pointing at the previous
        # agent's card even though model.options already reports the effective
        # owner.
        try:
            from gateway.rpc import model as model_rpc
            data = srv._read_providers()
            providers = data.get("providers") or []
            if providers:
                active_idx = model_rpc._active_index(data, providers)
                repaired = model_rpc._repair_agent_model(srv, providers, active_idx)
                if repaired != active_idx:
                    data["activeProviderIndex"] = repaired
                    srv._write_providers(data)
        except Exception as e:
            logger.warning(f"[Server] Failed to align provider with agent model: {e}")
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

    if method == "notebook.read":
        return {"id": rid, "result": {"entries": srv._load_notebook()}}

    if method == "notebook.write":
        entries = params.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        srv._save_notebook(entries)
        # 回读一次，前端拿到的是清理过的表（空条目已丢弃、缺 id 已补）
        return {"id": rid, "result": {"success": True, "entries": srv._load_notebook()}}

    # ==================== 外部链接 ====================

    if method == "system.open_url":
        url = params.get("url")
        if not isinstance(url, str):
            return {"id": rid, "error": {"code": -32000, "message": "url required"}}
        url = url.strip()
        # 只放行 http/https。file:、javascript:、data: 交给系统启动，等于把任意
        # 本地可执行目标递给了 shell，而链接来自模型输出，不能当可信输入。
        if not url.lower().startswith(("http://", "https://")):
            return {"id": rid, "error": {"code": -32000, "message": "only http and https links can be opened"}}
        if len(url) > 2048 or any(ch in url for ch in ("\x00", "\n", "\r")):
            return {"id": rid, "error": {"code": -32000, "message": "url rejected"}}
        try:
            if sys.platform == "win32":
                os.startfile(url)
            else:
                import webbrowser
                webbrowser.open(url, new=2)
        except Exception as e:
            logger.warning(f"[Server] Failed to open url: {e}")
            return {"id": rid, "error": {"code": -32000, "message": "could not open the link"}}
        return {"id": rid, "result": {"success": True}}

    # ==================== 文件变更面板 (File Changes) ====================

    return None
