"""Permission and path-whitelist RPCs."""

import json
import logging
import os

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "path_whitelist.read":
        whitelist_path = srv._DATA_DIR / "path_whitelist.json"
        bundled_whitelist = srv._PROJECT_ROOT / "path_whitelist.json"
        paths = []
        for wp in [whitelist_path, bundled_whitelist]:
            if wp.exists():
                try:
                    with open(wp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        paths = data.get("paths", [])
                        break
                except Exception as e:
                    logger.warning(f"[Server] Failed to read path_whitelist.json from {wp}: {e}")
        return {"id": rid, "result": {"paths": paths}}

    if method == "path_whitelist.save":
        whitelist_path = srv._DATA_DIR / "path_whitelist.json"
        new_paths = params.get("paths", [])
        if not isinstance(new_paths, list):
            return {"id": rid, "error": {"code": -32602, "message": "paths must be a list"}}
        if len(new_paths) > 5:
            return {"id": rid, "error": {"code": -32602, "message": "maximum 5 paths allowed"}}
        # 路径内容校验，防止"白名单盘根"关掉边界
        for p in new_paths:
            if not isinstance(p, str) or not p.strip():
                return {"id": rid, "error": {"code": -32602, "message": "paths entries must be non-empty strings"}}
            if not os.path.isabs(p):
                return {"id": rid, "error": {"code": -32602, "message": f"path must be absolute: {p}"}}
            stripped = os.path.abspath(p).rstrip("\\/")
            if not stripped or (len(stripped) == 2 and stripped[1] == ":"):
                return {"id": rid, "error": {"code": -32602, "message": f"path too broad (drive/filesystem root not allowed): {p}"}}
        try:
            data = {"paths": new_paths}
            if whitelist_path.exists():
                with open(whitelist_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                existing["paths"] = new_paths
                data = existing
            with open(whitelist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 同步推送给运行中的工具执行器
            try:
                svc = srv._ensure_service()
                svc._load_path_whitelist()
            except Exception:
                pass
            return {"id": rid, "result": {"ok": True, "paths": new_paths}}
        except Exception as e:
            logger.warning(f"[Server] Failed to write path_whitelist.json: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    # ==================== 权限管理 ====================

    if method == "permission.mode.get":
        """获取当前权限模式"""
        try:
            from gateway.permission_manager import get_permission_manager
            mgr = get_permission_manager()
            mode = mgr.get_mode().value if hasattr(mgr.get_mode(), 'value') else str(mgr.get_mode())
            return {"id": rid, "result": {"mode": mode}}
        except Exception as e:
            logger.warning(f"[Server] permission.mode.get error: {e}")
            return {"id": rid, "result": {"mode": "manual"}}

    if method == "permission.mode.set":
        """设置权限模式"""
        try:
            from gateway.permission_manager import get_permission_manager, PermissionMode, _LEGACY_MODES
            mode_str = params.get("mode", "manual")
            mode = _LEGACY_MODES.get(mode_str)
            if mode is None:
                mode = PermissionMode(mode_str)
            mode_str = mode.value
            mgr = get_permission_manager()
            mgr.set_mode(mode)
            # 模式切换写审计（auto 等高危切换可追溯）
            try:
                from gateway.permission_audit import get_audit_logger, AuditEvent, AuditEventType
                get_audit_logger().log_event(AuditEvent(
                    event_type=AuditEventType.MODE_CHANGED.value,
                    target=mode_str,
                    content=f"permission mode -> {mode_str}",
                    allowed=True,
                    decision_source="user_setting",
                    reason="permission.mode.set RPC",
                ))
            except Exception as audit_err:
                logger.warning(f"[Server] audit mode change failed: {audit_err}")
            logger.info(f"[Server] Permission mode set to: {mode_str}")
            return {"id": rid, "result": {"ok": True, "mode": mode_str}}
        except Exception as e:
            logger.warning(f"[Server] permission.mode.set error: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "permission.rules.get":
        """获取所有权限规则"""
        try:
            from gateway.permission_manager import get_permission_manager
            mgr = get_permission_manager()
            rules = mgr.get_all_rules()
            return {"id": rid, "result": rules}
        except Exception as e:
            logger.warning(f"[Server] permission.rules.get error: {e}")
            return {"id": rid, "result": {"mode": "default", "allow": [], "deny": [], "ask": []}}

    if method == "permission.check":
        """验证某个操作是否被允许（前端校验用；同步评估，不触发弹窗）"""
        try:
            from gateway.permission_manager import get_permission_manager
            tool_name = params.get("tool", "")
            content = params.get("content", "")
            mgr = get_permission_manager()
            allowed, msg = mgr.check_permission_sync(tool_name, content or None)
            return {"id": rid, "result": {"allowed": allowed, "message": msg}}
        except Exception as e:
            logger.warning(f"[Server] permission.check error: {e}")
            return {"id": rid, "result": {"allowed": False, "message": "Error checking permission"}}

    if method == "permission.decide":
        """用户对权限请求的决策"""
        try:
            import asyncio
            from gateway.permission_service import get_permission_service
            request_id = params.get("request_id", "")
            approved = params.get("approved", False)
            remember_minutes = params.get("remember_minutes", 0)
            svc = get_permission_service()
            loop = asyncio.get_event_loop()
            loop.create_task(svc.process_decision(request_id, approved, remember_minutes))
            logger.info(f"[Server] Permission decision: {request_id} approved={approved}")
            # Carried so the client can clear the prompt on the owning session
            # even when the decision was made from a different one.
            owner_sid = ""
            try:
                for r in svc.get_pending_requests():
                    if r.request_id == request_id:
                        owner_sid = r.session_id
                        break
            except Exception:
                pass
            # Tell every client, otherwise the other one keeps showing a sheet for
            # a request that has already been answered.
            srv.broadcast_event("permission.resolved", {
                "request_id": request_id,
                "session_id": owner_sid,
                "approved": bool(approved),
            })
            return {"id": rid, "result": {"ok": True}}
        except Exception as e:
            logger.warning(f"[Server] permission.decide error: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    if method == "permission.list":
        """Pending approvals, so a client that reconnects can rebuild its inbox.

        The service keeps undecided requests in memory only; without this the
        phone would show an empty inbox after a reconnect and let the request
        time out unseen.
        """
        try:
            from gateway.permission_service import get_permission_service, PERMISSION_LEVELS, PermissionLevel
            svc = get_permission_service()
            requests = []
            for r in svc.get_pending_requests():
                requests.append({
                    "request_id": r.request_id,
                    "session_id": r.session_id,
                    "permission_type": r.permission_type.value,
                    "target": r.target,
                    "reason": r.reason,
                    "timestamp": r.timestamp.isoformat(),
                    "level": PERMISSION_LEVELS.get(r.permission_type, PermissionLevel.MEDIUM).value,
                })
            return {"id": rid, "result": {"requests": requests}}
        except Exception as e:
            logger.warning(f"[Server] permission.list error: {e}")
            return {"id": rid, "result": {"requests": []}}

    if method == "permission.audit.verify":
        """校验权限审计日志完整性"""
        try:
            from gateway.permission_audit import get_audit_logger
            ok = get_audit_logger().verify_integrity()
            return {"id": rid, "result": {"ok": ok}}
        except Exception as e:
            logger.warning(f"[Server] permission.audit.verify error: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    # ==================== 配置管理 ====================

    return None
