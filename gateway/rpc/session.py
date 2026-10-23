"""Session, project-scoped file, and snapshot RPCs."""

import logging
import os
import time

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "session.create":
        project_path = params.get("project_path", "")
        session = srv._session_store.create(project_path=project_path)
        if project_path:
            srv._session_project_paths[session["session_id"]] = srv._norm_project_path(session["session_id"])
        # A session started on one client has to show up in the other one's list
        # without a manual refresh.
        srv.broadcast_event("session.created", {
            "session_id": session["session_id"],
            "project_path": project_path,
        })
        return {
        "id": rid,
        "result": {
        "session_id": session["session_id"],
        "info": {
        "model": ctx.active_model,
        "skills": srv.load_skills_info(),
        "tools": srv.load_tools_info(),
        }
        }
        }

    if method == "session.list":
        limit = params.get("limit", 200)
        project_path = params.get("project_path") or None
        sessions = srv._session_store.list(limit, project_path=project_path)
        return {"id": rid, "result": {"sessions": sessions}}

    if method == "session.most_recent":
        recent = srv._session_store.most_recent()
        return {"id": rid, "result": recent or {}}

    if method == "session.unassigned_count":
        return {"id": rid, "result": {"count": srv._session_store.unassigned_count()}}

    if method == "session.adopt_unassigned":
        project_path = srv._norm_project_path(params.get("project_path", ""))
        if not project_path:
            return {"id": rid, "error": {"code": -32000, "message": "project_path required"}}
        adopted = srv._session_store.adopt_unassigned(project_path)
        return {"id": rid, "result": {"adopted": adopted}}

    if method == "session.resume":
        sid = params.get("session_id", "")
        session = srv._session_store.get(sid)
        if not session:
            return {"id": rid, "error": {"code": -32000, "message": f"Session not found: {sid}"}}
        # 回填内存项目映射（重启后 agent 工具路径不丢）
        spath = session.get("project_path") or ""
        if spath:
            srv._session_project_paths[sid] = srv._norm_project_path(spath)
        else:
            srv._session_project_paths.pop(sid, None)
        messages = srv._session_store.get_messages(sid, limit=999999)
        cost_summary = srv._session_store.get_cost_summary(sid)
        # [DEBUG-TOOL] 打印后端返回的消息顺序，定位工具错位
        # print(f"[DEBUG-TOOL] session.resume: sid={sid}, total_messages={len(messages)}")
        # for i, m in enumerate(messages):
        #     role = m.get("role", "?")
        #     name = m.get("name", "")
        #     content_len = len(m.get("text") or m.get("content") or "")
        #     interrupted_flag = m.get("interrupted", "")
        #     print(f"[DEBUG-TOOL]   msg[{i}] role={role} name={name if role=='tool' else 'N/A'} content_len={content_len}" + (f" interrupted={interrupted_flag}" if role == 'assistant' else f" reasoning_len={len(m.get('reasoning') or '')}" if role == 'assistant' else ""))
        # [DEBUG-SERVER] 若有中断/消息恢复问题，取消下面注释
        # for i, m in enumerate(messages):
        #     if m.get("role") == "assistant" and "interrupted" in m:
        #         print(f"[DEBUG-SERVER] session.resume: messages[{i}] assistant interrupted={m['interrupted']}, content_len={len(m.get('text','') or '')}")
        # print(f"[DEBUG-SERVER] session.resume: total messages={len(messages)}, returning to frontend")
        return {
        "id": rid,
        "result": {
        "session_id": sid,
        "project_path": spath,
        "messages": messages,
        "message_count": session.get("message_count", 0),
        "cost_summary": cost_summary,
        # A turn may still be running; the client uses this to decide whether the
        # transcript is live instead of trusting event traffic that a dropped
        # socket would have eaten.
        "running": srv.is_session_running(sid),
        "info": {
        "model": ctx.active_model,
        "skills": srv.load_skills_info(),
        "tools": srv.load_tools_info(),
        }
        }
        }

    if method == "session.title":
        sid = params.get("session_id", "")
        title = params.get("title", "")
        if sid and title:
            srv._session_store.update_title(sid, title)
        return {"id": rid, "result": {"ok": True}}

    if method == "session.delete":
        sid = params.get("session_id", "")
        # 先取消该会话运行中的 agent：避免删除后 agent 继续写库
        # （曾导致：FK 崩溃 + agent_chat.db 孤儿消息 + 连接留锁连锁 "database is locked"）
        _ce = srv._cancel_events.get(sid)
        if _ce is not None:
            _ce.set()
            logger.info(f"[Server] Cancelled running agent before deleting session {sid}")
        ok = srv._session_store.delete(sid)
        if ok:
            # 彻底清理 agent_chat.db：消息（含无 msg_uuid 的）+ 成本记录 + 会话行
            try:
                from agent_core.agent_chat_history import delete_agent_conversation_full
                delete_agent_conversation_full(sid)
            except Exception as e:
                logger.warning(f"[Server] Failed to delete agent_chat data for session {sid}: {e}")
        return {"id": rid, "result": {"deleted": sid} if ok else {}}

    if method == "session.info":
        sid = params.get("session_id", "")
        session = srv._session_store.get(sid)
        if session:
            return {
            "id": rid,
            "result": {
            "session_id": sid,
            "info": {
            "title": session.get("title", ""),
            "message_count": session.get("message_count", 0),
            "model": ctx.active_model,
            "skills": srv.load_skills_info(),
            "tools": srv.load_tools_info(),
            }
            }
            }
        return {"id": rid, "result": {}}

    if method == "session.search":
        q = params.get("q", "").strip()
        limit = params.get("limit", 20)
        if not q:
            return {"id": rid, "result": {"results": [], "total": 0}}
        results = srv._session_store.search_messages(q, limit)
        return {
        "id": rid,
        "result": {"results": results, "total": len(results)},
        }

    if method == "session.export":
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        md = srv._session_store.export_markdown(sid)
        if md is None:
            return {"id": rid, "error": {"code": -32000, "message": "Session not found or empty"}}
        return {"id": rid, "result": {"markdown": md, "session_id": sid}}

    if method == "session.export_save":
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        md = srv._session_store.export_markdown(sid)
        if md is None:
            return {"id": rid, "error": {"code": -32000, "message": "Session not found or empty"}}
        session = srv._session_store.get(sid)
        title = (session or {}).get("title") or "New conversation"
        safe_title = "".join(c if c.isalnum() or c in " _-." else "_" for c in title)[:60]
        project_path = getattr(srv._ensure_service(), '_current_project_path', None) or str(srv._PROJECT_ROOT)
        history_dir = os.path.join(project_path, "history")
        os.makedirs(history_dir, exist_ok=True)
        filename = f"{safe_title}_{int(time.time())}.md"
        filepath = os.path.join(history_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md)
            logger.info(f"[Server] Exported session {sid} to {filepath}")
            return {"id": rid, "result": {"filepath": filepath, "session_id": sid}}
        except Exception as e:
            logger.error(f"[Server] Export save failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "session.compress":
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        raw_messages = srv._session_store.get_messages_for_context(sid, limit=999999)
        if not raw_messages:
            return {"id": rid, "result": {"success": False, "reason": "no messages"}}
        try:
            from agent_core.context_compressor import manual_compress
            client, model = srv._get_llm_client(sid)

            # 转换消息格式：get_messages 返回的格式 → manual_compress 期望的格式
            messages = []
            for m in raw_messages:
                role = m.get("role", "user")
                content = m.get("text") or m.get("content") or ""
                if role == "tool":
                    messages.append({
                    "role": "tool",
                    "content": m.get("context") or "",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "name": m.get("name", "tool"),
                    })
                elif role == "assistant":
                    msg = {"role": "assistant", "content": content}
                    if m.get("reasoning"):
                        msg["reasoning_content"] = m["reasoning"]
                    messages.append(msg)
                else:
                    messages.append({"role": role, "content": content})

            result = manual_compress(
            messages=messages,
            max_tokens=30000,
            recent_messages=2,
            llm_client=client,
            llm_model=model,
            )

            if result.get("success") and result.get("compressed_messages"):
                compressed = result["compressed_messages"]
                boundary = next(
                (
                msg for msg in compressed
                if msg.get("role") == "system"
                and (msg.get("content") or "").startswith("[COMPACT_BOUNDARY:")
                ),
                None
                )
                summary = next(
                (
                msg for msg in compressed
                if msg.get("is_compact_summary")
                ),
                None
                )
                if boundary and summary:
                    kept_count = max(0, len(compressed) - 2)  # 减去 boundary 和 summary
                    kept_agent = max(1, sum(
                        1 for msg in compressed
                        if msg.get("role") != "system"
                        and not msg.get("is_compact_summary")
                    ))

                    # sessions.db: 插入 marker，不删除旧消息
                    saved = srv._session_store.insert_compaction_result(sid, boundary, summary, kept_count)
                    if saved:
                        logger.info(f"[Server] Compressed session {sid}: inserted markers into sessions.db (kept {kept_count})")
                    else:
                        logger.warning(f"[Server] Compressed session {sid}: sessions.db marker insert failed")

                    # agent_chat.db: 同步插入 marker
                    try:
                        from agent_core import agent_chat_history as history
                        conv = history.get_agent_conversation_by_uuid(sid)
                        if conv:
                            # 注意：不再传 kept_count——agent_chat.db 是精简库（只存 user/assistant），
                            # 用它推算 cutoff 会导致标记范围错误（历史 bug：标记 0 条、上下文不缩小）。
                            # agent_chat 版 insert 内部按"boundary 之前全部标记"语义处理。
                            ok = history.insert_compaction_result(conv["id"], boundary, summary, kept_agent)
                            if ok:
                                logger.info(f"[Server] Compressed session {sid}: synced markers to agent_chat.db")
                            else:
                                logger.warning(f"[Server] Compressed session {sid}: agent_chat.db marker insert failed")
                    except Exception as e:
                        logger.warning(f"[Server] Compressed session {sid}: agent_chat.db sync error: {e}")
                else:
                    logger.warning(
                    f"[Server] Compressed session {sid}: missing boundary/summary in compressed_messages "
                    f"(count={len(compressed)})"
                    )
            else:
                logger.info(f"[Server] Compress result for {sid}: success={result.get('success')}, "
                f"reason={result.get('reason', 'none')}")

            logger.info(f"[Server] Compressed session {sid}: "
            f"{result.get('before', {}).get('tokens', '?')} -> "
            f"{result.get('after', {}).get('tokens', '?')} tokens")

            return {"id": rid, "result": result}
        except Exception as e:
            logger.error(f"[Server] Compress failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "session.interrupt":
        sid = params.get("session_id", "")
        target_sids = [sid] if sid else list(srv._cancel_events.keys() | srv._running_prompt_futures.keys())
        for target_sid in target_sids:
            event = srv._cancel_events.get(target_sid)
            if event:
                event.set()

            # 真正取消正在 await LLM stream / tool 的 asyncio task，而不是只等下一块输出。
            future = srv._running_prompt_futures.get(target_sid)
            if future and not future.done():
                future.cancel()

            try:
                srv._ensure_service().cancel_chat(target_sid)
            except Exception as e:
                logger.warning(f"[Server] cancel_chat failed: {e}")
        return {"id": rid, "result": {"ok": True}}

    if method == "session.file_changes":
        """获取当前会话的所有文件变更列表"""
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            from agent_core.snapshot.snapshot_diff import DiffEngine
            project_path = params.get("project_path", "") or srv._session_project_paths.get(sid, str(srv._PROJECT_ROOT))
            if params.get("project_path"):
                srv._session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "result": {"files": [], "total_files": 0, "total_additions": 0, "total_deletions": 0}}
            records = manager.query_all_active()
            if not records:
                return {"id": rid, "result": {"files": [], "total_files": 0, "total_additions": 0, "total_deletions": 0}}
            actual_project = getattr(manager, 'project_path', project_path)
            # 每个文件当前可回退的版本数（活跃快照条数）
            active_counts = {}
            for r in records:
                if r.revert_status == 'active':
                    key = srv._resolve_snapshot_path(r.relative_path, actual_project)
                    active_counts[key] = active_counts.get(key, 0) + 1
            diff_result = DiffEngine.compute_diff(records, actual_project)
            files_out = []
            total_add = 0
            total_del = 0
            for ch in diff_result.get('changes', []):
                add_count = sum(1 for h in ch.get('hunks', []) for l in h.get('lines', []) if l.get('prefix') == '+')
                del_count = sum(1 for h in ch.get('hunks', []) for l in h.get('lines', []) if l.get('prefix') == '-')
                total_add += add_count
                total_del += del_count
                file_key = srv._resolve_snapshot_path(ch['file'], actual_project)
                files_out.append({
                "relative_path": ch['file'],
                "status": ch['status'],
                "message": ch.get('message', ''),
                "added_lines": add_count,
                "removed_lines": del_count,
                "active_count": active_counts.get(file_key, 1),
                })
            # 按路径排序
            files_out.sort(key=lambda f: f['relative_path'])
            return {"id": rid, "result": {
            "files": files_out,
            "total_files": len(files_out),
            "total_additions": total_add,
            "total_deletions": total_del,
            }}
        except Exception as e:
            logger.error(f"[Server] session.file_changes failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "session.file_accept":
        """接受所有文件变更（将活跃快照标记为已接受）"""
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            project_path = params.get("project_path", "") or srv._session_project_paths.get(sid, str(srv._PROJECT_ROOT))
            if params.get("project_path"):
                srv._session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "result": {"accepted": 0}}
            accepted = manager.accept_all_active()
            logger.info(f"[Server] Accepted {accepted} file changes for session {sid}")
            # The other client keeps its own change list; without this it goes on
            # showing freshly accepted files as pending.
            srv.broadcast_event("files.accepted", {"session_id": sid, "accepted": accepted})
            return {"id": rid, "result": {"accepted": accepted}}
        except Exception as e:
            logger.error(f"[Server] session.file_accept failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "session.file_diff":
        """获取某个文件的详细 diff"""
        sid = params.get("session_id", "")
        raw_path = params.get("relative_path", "")
        if not sid or not raw_path:
            return {"id": rid, "error": {"code": -32000, "message": "session_id and relative_path required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            from agent_core.snapshot.snapshot_diff import DiffEngine
            project_path = params.get("project_path", "") or srv._session_project_paths.get(sid, str(srv._PROJECT_ROOT))
            if params.get("project_path"):
                srv._session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "result": {"file": raw_path, "status": "error", "hunks": [], "message": "Snapshot manager not available"}}
            # 使用 manager 实例的 project_path，保证与快照记录一致
            actual_project = getattr(manager, 'project_path', project_path)
            # 取所有活跃记录，只做完整路径相等匹配
            all_records = manager.query_all_active()
            target_abs = srv._resolve_snapshot_path(raw_path, actual_project)
            matched = [r for r in all_records if srv._snapshot_record_matches(r, target_abs, actual_project)]
            if not matched:
                return {"id": rid, "result": {"file": raw_path, "status": "noop", "hunks": [], "message": "No snapshots found for this file"}}
            diff_result = DiffEngine.compute_diff(matched, actual_project)
            for ch in diff_result.get('changes', []):
                if srv._snapshot_path_equal(ch['file'], target_abs, actual_project):
                    return {"id": rid, "result": ch}
            return {"id": rid, "result": {"file": raw_path, "status": "noop", "hunks": [], "message": "No diff available from compute_diff"}}
        except Exception as e:
            logger.error(f"[Server] session.file_diff failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "session.file_revert":
        """回退单个文件"""
        sid = params.get("session_id", "")
        raw_path = params.get("relative_path", "")
        if not sid or not raw_path:
            return {"id": rid, "error": {"code": -32000, "message": "session_id and relative_path required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            from agent_core.snapshot.snapshot_executor import RevertExecutor
            project_path = params.get("project_path", "") or srv._session_project_paths.get(sid, str(srv._PROJECT_ROOT))
            if params.get("project_path"):
                srv._session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "error": {"code": -32000, "message": "Snapshot manager not available"}}
            actual_project = getattr(manager, 'project_path', project_path)
            target_abs = srv._resolve_snapshot_path(raw_path, actual_project)
            executor = RevertExecutor(manager)
            # 同一文件的回退串行执行，避免并发点击时重复恢复同一条记录
            with manager._file_revert_lock:
                all_records = manager.query_all_active()
                records = [r for r in all_records if srv._snapshot_record_matches(r, target_abs, actual_project)]
                target = None
                for r in records:
                    if r.revert_status == 'active':
                        target = r
                        break
                if not target:
                    return {"id": rid, "result": {"success": False, "message": "已撤销或找不到快照记录"}}
                manager.mark_records_as_reverting([target.record_id])
                try:
                    result = executor._restore_single_file(target)
                except Exception as restore_error:
                    manager.mark_records_as_active([target.record_id])
                    logger.error(f"[Server] session.file_revert restore failed: {restore_error}")
                    return {"id": rid, "result": {"success": False, "message": f"回退失败: {restore_error}"}}
                if result.status != "success":
                    # 恢复失败：把记录放回活跃状态，允许用户重试
                    manager.mark_records_as_active([target.record_id])
                    return {"id": rid, "result": {"success": False, "message": result.message}}
                manager.mark_records_as_reverted([target.record_id], sid + "_manual")
                remaining = sum(
                    1 for r in manager.query_all_active()
                    if srv._snapshot_record_matches(r, target_abs, actual_project)
                )
                # Let the other client drop the file from its change list too.
                srv.broadcast_event("files.reverted", {"session_id": sid, "relative_path": raw_path, "remaining": remaining})
                return {"id": rid, "result": {"success": True, "message": result.message, "remaining": remaining}}
        except Exception as e:
            logger.error(f"[Server] session.file_revert failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "session.file_revert_all":
        """批量回退到某条消息"""
        sid = params.get("session_id", "")
        message_id = params.get("message_id", 0)
        conflict_strategy = params.get("conflict_strategy", "abort")
        if not sid or not message_id:
            return {"id": rid, "error": {"code": -32000, "message": "session_id and message_id required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            from agent_core.snapshot.snapshot_executor import RevertExecutor
            project_path = params.get("project_path", "") or srv._session_project_paths.get(sid, str(srv._PROJECT_ROOT))
            if params.get("project_path"):
                srv._session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "error": {"code": -32000, "message": "Snapshot manager not available"}}
            executor = RevertExecutor(manager)
            return {"id": rid, "result": executor.restore_batch(message_id, conflict_strategy)}
        except Exception as e:
            logger.error(f"[Server] session.file_revert_all failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    return None


    return None
