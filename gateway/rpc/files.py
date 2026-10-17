"""File read and search RPCs."""

import logging
import os

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "file.search":
        """搜索项目目录中的文件（自动补全用；目录受项目边界/白名单限制）"""
        q = params.get("q", "").strip().lower()
        path = params.get("path", str(srv._PROJECT_ROOT))
        try:
            limit = max(1, min(int(params.get("limit", 20)), 1000))
        except Exception:
            limit = 20
        ok, err, resolved = srv._guard_rpc_path(path)
        if not ok:
            return {"id": rid, "result": {"results": [], "error": err}}
        path = resolved
        results = []
        try:
            if os.path.isdir(path):
                for entry in os.listdir(path):
                    if q and q not in entry.lower():
                        continue
                    full = os.path.join(path, entry)
                    results.append({
                    "name": entry,
                    "path": full,
                    "type": "directory" if os.path.isdir(full) else "file",
                    })
                    if len(results) >= limit:
                        break
        except PermissionError:
            pass
        except Exception as e:
            logger.warning(f"[Server] file.search failed: {e}")
        return {"id": rid, "result": {"results": results}}

    if method == "file.read_text":
        """读取文本文件内容（路径受项目边界/白名单限制）"""
        path = params.get("path", "")
        ok, err, resolved = srv._guard_rpc_path(path)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        path = resolved
        try:
            if not os.path.exists(path):
                return {"id": rid, "result": {"ok": False, "error": "文件不存在"}}
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            return {"id": rid, "result": {"ok": True, "text": text}}
        except Exception as e:
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "file.read_base64":
        """读取文件内容，返回 base64 和文本内容（路径受项目边界/白名单限制）"""
        import base64 as _b64
        path = params.get("path", "")
        ok, err, resolved = srv._guard_rpc_path(path)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        path = resolved
        try:
            if not os.path.exists(path):
                return {"id": rid, "result": {"ok": False, "error": "文件不存在"}}
            with open(path, "rb") as f:
                raw = f.read()
            b64 = _b64.b64encode(raw).decode("ascii")
            tc = None
            try:
                tc = raw.decode("utf-8")
            except Exception:
                pass
            return {"id": rid, "result": {"ok": True, "data_base64": b64, "text_content": tc, "size": len(raw)}}
        except Exception as e:
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "file.write_base64":
        """写入 base64 数据到文件（PNG 保存等场景；路径受项目边界/白名单限制）"""
        import base64 as _b64
        path = params.get("path", "")
        data_b64 = params.get("data_base64", "")
        if not path:
            return {"id": rid, "result": {"ok": False, "error": "path required"}}
        if not data_b64:
            return {"id": rid, "result": {"ok": False, "error": "data_base64 required"}}
        ok, err, resolved = srv._guard_rpc_path(path)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        path = resolved
        try:
            # 确保父目录存在
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            raw = _b64.b64decode(data_b64)
            # 原子写：先写临时文件再替换，避免写一半损坏
            tmp_path = path + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(raw)
            os.replace(tmp_path, path)
            logger.info(f"[Server] file.write_base64: wrote {len(raw)} bytes to {path}")
            return {"id": rid, "result": {"ok": True, "path": path, "size": len(raw)}}
        except Exception as e:
            logger.warning(f"[Server] file.write_base64 failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "file.open":
        """用系统默认程序打开文件或 URL（本地路径受项目边界/白名单限制）"""
        import subprocess
        import sys
        path = params.get("path", "")
        ok, err, resolved = srv._guard_rpc_path(path, allow_url=True)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        path = resolved
        try:
            is_url = path.startswith(("http://", "https://", "ftp://"))
            if not is_url and not os.path.exists(path):
                return {"id": rid, "result": {"ok": False, "error": "文件或 URL 不存在"}}
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return {"id": rid, "result": {"ok": True}}
        except Exception as e:
            logger.warning(f"[Server] file.open failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    return None
