"""File read and search RPCs."""

import logging
import os

logger = logging.getLogger(__name__)

# The preview panel reads the user's own repository, which holds build output
# and multi-hundred-megabyte logs as readily as it holds source. A read that
# walks the whole file would travel back over the socket and land in the
# renderer in one piece, so both paths are capped. The text ceiling matches the
# GitHub pane's own 1 MiB refusal in spirit: past a few hundred kilobytes the
# highlighter, not the disk, is what stalls.
_MAX_TEXT_BYTES = 512 * 1024
_MAX_BASE64_BYTES = 16 * 1024 * 1024


def _decode_text(raw: bytes) -> str:
    """Decode a file as UTF-8, falling back to GBK, then to lossy UTF-8.

    A Windows project keeps GBK sources and notes next to UTF-8 ones, and a
    strict UTF-8 decode turned every one of them into an RPC error. The byte
    peel covers the other case: a `max_bytes` cap that lands inside a multi-byte
    character would otherwise leave a trailing replacement mark, or fail the
    whole decode. UTF-16 is not handled here on purpose - its NUL bytes mark it
    binary before this runs.
    """
    for encoding in ("utf-8-sig", "gbk"):
        for cut in range(4):
            chunk = raw[: len(raw) - cut] if cut else raw
            try:
                return chunk.decode(encoding)
            except UnicodeDecodeError:
                continue
    return raw.decode("utf-8", "replace")


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "file.search":
        """搜索项目目录中的文件（自动补全用；目录受项目边界/白名单限制）"""
        q = params.get("q", "").strip().lower()
        # The project panel's tree asks without a path, which has to mean "the
        # active project". The program root is not an answer: in a packaged
        # build it is the install directory, so the tree listed the app's own
        # files and the guard rejected the rest.
        path = params.get("path") or ""
        if not path:
            try:
                path = (srv._read_projects() or {}).get("active") or ""
            except Exception:
                path = ""
        if not path:
            return {"id": rid, "result": {"results": [], "error": "no active project"}}
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
        """读取文本文件内容（路径受项目边界/白名单限制）

        返回值带着 size / binary / truncated：预览面板要能区分"空文件"、
        "二进制"和"只给了前一段"，否则三者都显示成一片空白。二进制判定与
        rpc/github.py 同一条规则 —— 头部 8 KiB 里有 NUL 字节即二进制，避免把
        编译产物解成一屏替换符。解码用 errors="replace"，严格 utf-8 会让
        GBK 或其他 ANSI 编码的中文文件整个请求变成错误。
        """
        path = params.get("path", "")
        ok, err, resolved = srv._guard_rpc_path(path)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        path = resolved
        if not os.path.exists(path):
            return {"id": rid, "result": {"ok": False, "error": "文件不存在"}}
        if not os.path.isfile(path):
            return {"id": rid, "result": {"ok": False, "error": "不是一个文件"}}
        try:
            cap = max(1024, min(int(params.get("max_bytes") or _MAX_TEXT_BYTES), _MAX_TEXT_BYTES))
        except Exception:
            cap = _MAX_TEXT_BYTES
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                head = f.read(8192)
                if b"\x00" in head:
                    return {"id": rid, "result": {
                        "ok": True, "text": "", "size": size,
                        "binary": True, "truncated": False}}
                f.seek(0)
                raw = f.read(cap + 1)
            truncated = len(raw) > cap
            return {"id": rid, "result": {
                "ok": True,
                "text": _decode_text(raw[:cap]),
                "size": size,
                "binary": False,
                "truncated": truncated,
            }}
        except PermissionError:
            return {"id": rid, "result": {"ok": False, "error": "没有读取权限"}}
        except Exception as e:
            logger.warning(f"[Server] file.read_text failed: {e}")
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
            size = os.path.getsize(path)
            # base64 inflates by a third, so an unbounded read doubles the cost
            # of an already oversized file. Refuse instead of trying.
            if size > _MAX_BASE64_BYTES:
                limit_mb = _MAX_BASE64_BYTES // (1024 * 1024)
                return {"id": rid, "result": {
                    "ok": False,
                    "error": f"文件 {size // (1024 * 1024)} MB，超过 {limit_mb} MB 上限，未加载"}}
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
