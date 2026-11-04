"""Project registry RPCs."""

import logging

logger = logging.getLogger(__name__)

# A corrupt registry answers "no projects", and a save would turn that read
# error into the truth. Until the file is repaired these two methods refuse
# instead of writing on top of an unknown state; the original bytes are kept
# next to it as projects.json.corrupt.
_CORRUPT_MSG = (
    "projects.json is unreadable; a copy was kept as projects.json.corrupt. "
    "Fix or remove the file before changing the project list"
)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "project.list":
        data = srv._read_projects()
        # `corrupt` is passed through so the panel can say the registry is
        # unreadable instead of rendering it as "no projects".
        return {"id": rid, "result": {
            "projects": data["projects"],
            "active": data["active"],
            "corrupt": bool(data.get("corrupt")),
            "backup": str(data.get("backup") or ""),
        }}

    if method == "project.add":
        path = srv._norm_project_path(params.get("path", ""))
        if not path:
            return {"id": rid, "error": {"code": -32000, "message": "path required"}}
        ok, err, resolved = srv._validate_project_dir(path)
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": err}}
        path = resolved
        data = srv._read_projects()
        if data.get("corrupt"):
            return {"id": rid, "error": {"code": -32000, "message": _CORRUPT_MSG}}
        if path not in data["projects"]:
            data["projects"].append(path)
            if not data["active"]:
                data["active"] = path
            srv._write_projects(data)
        # `added` is the path the gateway actually stored. The client used to
        # guess it by string-matching its own input and falling back to the last
        # row, which opened an unrelated project whenever the two spellings
        # differed (realpath resolves case and links).
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"], "added": path}}

    if method == "project.delete":
        path = srv._norm_project_path(params.get("path", ""))
        data = srv._read_projects()
        if data.get("corrupt"):
            return {"id": rid, "error": {"code": -32000, "message": _CORRUPT_MSG}}
        if path in data["projects"]:
            data["projects"].remove(path)
            if data["active"] == path:
                data["active"] = data["projects"][0] if data["projects"] else ""
            srv._write_projects(data)
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.set_active":
        path = srv._norm_project_path(params.get("path", ""))
        if not path:
            return {"id": rid, "error": {"code": -32000, "message": "path required"}}
        # A running turn keeps issuing tool calls against the service's current
        # root; switching underneath it writes files into the new project
        # halfway through the reply. The check is global on purpose: a turn
        # started by another client moves too, and a request that omits the
        # session id must not slip past the guard.
        if srv.is_any_session_running():
            return {"id": rid, "error": {"code": -32000, "message": "a reply is still running; stop it before switching projects"}}
        data = srv._read_projects()
        if path in data["projects"]:
            # 校验：历史列表可能含盘根/失效路径，切换时复核
            ok, err, resolved = srv._validate_project_dir(path)
            if not ok:
                return {"id": rid, "error": {"code": -32000, "message": err}}
            previous = data.get("active") or ""
            data["active"] = resolved
            srv._write_projects(data)
            try:
                srv._ensure_service().set_project_path(resolved)
            except Exception as e:
                # Registry and runtime must not disagree: a failed sync used to be
                # swallowed while the RPC still answered ok, so the panel showed a
                # project the tools had never moved to.
                logger.error(f"[Server] project.set_active service sync failed: {e}")
                data["active"] = previous
                try:
                    srv._write_projects(data)
                except Exception as rollback_err:
                    logger.error(f"[Server] project.set_active rollback failed: {rollback_err}")
                return {"id": rid, "error": {"code": -32000, "message": f"could not apply project switch: {e}"}}
            return {"id": rid, "result": {"ok": True, "active": resolved}}
        return {"id": rid, "error": {"code": -32000, "message": "project not in list"}}

    if method == "project.set_path":
        path = params.get("path", "")
        if not path:
            return {"id": rid, "result": {"ok": False, "error": "path is required"}}
        # 校验：必须为存在的目录，且拒绝盘根（否则全部路径边界检查失效）
        ok, err, resolved = srv._validate_project_dir(path)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        service = srv._ensure_service()
        service.set_project_path(resolved)
        logger.info(f"[Server] Project path changed to: {resolved}")
        return {"id": rid, "result": {"ok": True, "path": resolved}}

    return None
