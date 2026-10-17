"""Project registry RPCs."""

import logging
import os

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "project.list":
        data = srv._read_projects()
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.add":
        path = srv._norm_project_path(params.get("path", ""))
        if not path:
            return {"id": rid, "error": {"code": -32000, "message": "path required"}}
        ok, err, resolved = srv._validate_project_dir(path)
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": err}}
        path = resolved
        data = srv._read_projects()
        if path not in data["projects"]:
            data["projects"].append(path)
            if not data["active"]:
                data["active"] = path
            srv._write_projects(data)
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.delete":
        path = srv._norm_project_path(params.get("path", ""))
        data = srv._read_projects()
        if path in data["projects"]:
            data["projects"].remove(path)
            if data["active"] == path:
                data["active"] = data["projects"][0] if data["projects"] else ""
            srv._write_projects(data)
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.set_active":
        path = srv._norm_project_path(params.get("path", ""))
        data = srv._read_projects()
        if path in data["projects"]:
            # 校验：历史列表可能含盘根/失效路径，切换时复核
            ok, err, resolved = srv._validate_project_dir(path)
            if not ok:
                return {"id": rid, "error": {"code": -32000, "message": err}}
            data["active"] = resolved
            srv._write_projects(data)
            try:
                srv._ensure_service().set_project_path(resolved)
            except Exception as e:
                logger.warning(f"[Server] project.set_active service sync failed: {e}")
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
        os.environ["PEBBLE_CWD"] = resolved
        os.environ["HERMES_CWD"] = resolved
        return {"id": rid, "result": {"ok": True, "path": resolved}}

    return None
