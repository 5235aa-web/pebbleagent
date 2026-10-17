import asyncio
import concurrent.futures
import copy
import json
import logging
import os
import sys
import threading
import time
import uuid
from asyncio import CancelledError
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union


def _num(v) -> Optional[float]:
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _bool(v, default: Optional[bool] = None) -> Optional[bool]:
    """宽松布尔解析：识别 bool 与 'true'/'false'/'1'/'0'/'yes'/'no'/'on'/'off'，无法识别时返回 default"""
    if v is None or v == '':
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ('true', '1', 'yes', 'on'):
        return True
    if s in ('false', '0', 'no', 'off'):
        return False
    return default


_PRICING_KEYS = ("input_per_million", "cache_hit_per_million", "output_per_million")


def _validate_pricing_block(block) -> tuple:
    """校验价格块：三个字段齐全、有限非负数值。返回 (ok, error_message)"""
    if not isinstance(block, dict):
        return False, "pricing block must be an object"
    for k in _PRICING_KEYS:
        v = block.get(k)
        if v is None:
            return False, f"missing field: {k}"
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False, f"{k} must be a number"
        if fv != fv or fv == float("inf") or fv == float("-inf") or fv < 0:
            return False, f"{k} must be a finite non-negative number"
    return True, ""


def _atomic_write_json(path: Path, data: dict):
    """原子写 JSON：先写临时文件再 os.replace，防止写一半崩溃损坏原文件"""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ==================== file.* RPC 路径守卫 ====================

def _get_current_project_root() -> str:
    """当前激活项目目录（与工具执行器一致；服务未初始化时回退打包根）"""
    try:
        svc = _ensure_service()
        cur = getattr(svc, "_current_project_path", None)
        if cur:
            return os.path.realpath(cur)
    except Exception:
        pass
    return os.path.realpath(str(_PROJECT_ROOT))


def _read_whitelist_paths() -> list:
    """读取路径白名单（用户数据目录优先，回退打包根）"""
    for wp in [_DATA_DIR / "path_whitelist.json", _PROJECT_ROOT / "path_whitelist.json"]:
        if wp.exists():
            try:
                with open(wp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                paths = data.get("paths", [])
                if isinstance(paths, list):
                    return [str(p) for p in paths if p]
            except Exception:
                continue
    return []


def _guard_rpc_path(path: str, allow_url: bool = False) -> tuple:
    """file.* RPC 的统一路径守卫。

    解析后的路径必须位于当前项目目录或路径白名单内（realpath 解析符号链接）。
    allow_url=True 时放行 http/https/ftp 链接（file.open 打开浏览器的场景）。
    返回 (ok, error_message, resolved_path)。
    """
    if not path or not isinstance(path, str):
        return False, "path required", ""
    if path.startswith(("http://", "https://", "ftp://")):
        if allow_url:
            return True, "", path
        return False, "url is not allowed for this operation", path
    try:
        project_root = _get_current_project_root()
        p = os.path.expandvars(os.path.expanduser(path))
        if not os.path.isabs(p):
            p = os.path.join(project_root, p)
        p = os.path.realpath(p)

        def _within(child: str, parent: str) -> bool:
            try:
                return os.path.commonpath([os.path.normcase(child), os.path.normcase(parent)]) == os.path.normcase(parent)
            except ValueError:
                return False

        if _within(p, project_root):
            return True, "", p
        for wl in _read_whitelist_paths():
            try:
                wlr = os.path.realpath(os.path.expandvars(os.path.expanduser(wl)))
            except Exception:
                continue
            if _within(p, wlr):
                return True, "", p
        return False, f"path is outside the project directory: {path}", p
    except Exception as e:
        return False, f"path check failed: {e}", ""


# ==================== MCP 自动安装辅助 ====================

def _detect_arch() -> str:
    """检测 CPU 架构（amd64 / arm64）"""
    import platform as _platform
    m = _platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "amd64"


def _download_file(url: str, dest: str, max_bytes: int = 200 * 1024 * 1024, timeout: int = 120) -> None:
    """流式下载到本地文件（带大小上限与超时保护）"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "PebbleAgent"})
    total = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(f"download exceeds size limit ({max_bytes} bytes)")
            f.write(chunk)


def _sha256_file(path: str) -> str:
    """流式计算文件 sha256（十六进制小写）"""
    import hashlib as _hashlib
    h = _hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _resolve_github_release_asset(api_url: str, pattern: str) -> tuple:
    """从 GitHub releases API 解析匹配 pattern 的资产，返回 (asset_name, download_url)"""
    import re as _re
    import urllib.request
    req = urllib.request.Request(api_url, headers={
        "User-Agent": "PebbleAgent",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if _re.search(pattern, name):
            return name, asset.get("browser_download_url", "")
    raise RuntimeError(f"no release asset matches pattern '{pattern}'")


def _safe_extract_zip(zip_path: str, dest_dir: str) -> list:
    """安全解压 zip（防路径穿越），返回解压出的文件绝对路径列表"""
    import zipfile
    extracted = []
    dest_root = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = os.path.realpath(os.path.join(dest_dir, info.filename))
            if not (target == dest_root or target.startswith(dest_root + os.sep)):
                raise RuntimeError(f"unsafe zip entry: {info.filename}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                import shutil as _shutil
                _shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted


def _install_download_server(server_name: str, entry: dict) -> str:
    """执行 download 类型 MCP 服务器的自动安装（下载 + 解压 + 落位），返回 exe 绝对路径"""
    import re as _re
    import zipfile
    url = str(entry.get("url") or "").strip()
    install_dir = os.path.expanduser(str(entry.get("install_dir") or "~/.pebble/mcp"))
    exe_name = str(entry.get("exe_name") or "").strip()
    asset_pattern = str(entry.get("asset_pattern") or "").strip()
    if not url or not exe_name:
        raise RuntimeError("mcp_install.json entry missing url or exe_name")
    os.makedirs(install_dir, exist_ok=True)
    exe_target = os.path.join(install_dir, exe_name)

    # GitHub releases 页面/API → 解析最新版本资产直链
    m = _re.search(r"github\.com/([\w.-]+)/([\w.-]+)/releases", url)
    if m:
        api_url = f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/releases/latest"
        pattern = (asset_pattern or ".*").replace("{arch}", _detect_arch())
        _, url = _resolve_github_release_asset(api_url, pattern)

    tmp_dl = os.path.join(install_dir, f"_download_{server_name}.tmp")
    logger.info(f"[Server] Downloading MCP server '{server_name}' from {url}")
    try:
        _download_file(url, tmp_dl)
        # 完整性校验：mcp_install.json 配置了 sha256 则强制比对；未配置则记录警告
        expected = str(entry.get("sha256") or "").strip().lower()
        if expected:
            actual = _sha256_file(tmp_dl)
            if actual != expected:
                raise RuntimeError(
                    f"sha256 mismatch for '{server_name}': expected {expected[:16]}..., got {actual[:16]}... "
                    f"(download rejected; verify the mcp_install.json entry or the release source)"
                )
            logger.info(f"[Server] MCP '{server_name}' sha256 verified ({actual[:16]}...)")
        else:
            # 未配置校验和 → 拒绝自动下载（网络下载的二进制必须校验；
            # 手动 path 模式不受影响，或向 mcp_install.json 补充 release hash 后启用）
            raise RuntimeError(
                f"'{server_name}' has no sha256 configured in mcp_install.json; automatic download refused. "
                f"Add the release hash to mcp_install.json, or install manually via the 'path' option."
            )
        # zip → 解压找 exe；否则视为裸 exe 直接落位
        try:
            files = _safe_extract_zip(tmp_dl, install_dir)
        except zipfile.BadZipFile:
            os.replace(tmp_dl, exe_target)
            return exe_target
        for f in files:
            if os.path.basename(f).lower() == exe_name.lower():
                if os.path.realpath(f) != os.path.realpath(exe_target):
                    if os.path.exists(exe_target):
                        os.remove(exe_target)
                    os.replace(f, exe_target)
                return exe_target
        raise RuntimeError(f"exe '{exe_name}' not found in downloaded archive")
    finally:
        if os.path.exists(tmp_dl):
            try:
                os.remove(tmp_dl)
            except OSError:
                pass


from .config import load_llm_config, load_tools_info, load_skills_info
from .session import SessionStore

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_core.agent_chat_service_v2 import AgentChatServiceV2, current_pricing_period, get_cn_holidays_payload
from agent_core.agent_loader import list_available_agents_from_yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(Path.home(), ".pebble")
os.makedirs(_DATA_HOME, exist_ok=True)
_DATA_DIR = Path(_DATA_HOME)
_PROVIDERS_PATH = _DATA_DIR / "llm_providers.json"
_AGENT_MODEL_PATH = _DATA_DIR / "agent_model_config.json"
_PROJECTS_PATH = _DATA_DIR / "projects.json"
_BUNDLED_PROVIDERS_PATH = _PROJECT_ROOT / "llm_providers.json"
_BUNDLED_AGENT_MODEL_PATH = _PROJECT_ROOT / "agent_model_config.json"

_session_store = SessionStore()
_session_project_paths: dict = {}  # sid → project_path，由 prompt.submit 写入，查询 API 读取
_current_service: Optional[AgentChatServiceV2] = None
# WebSocket 服务器的事件循环引用（由 ws_server.py 设置）
_ws_loop: Optional[asyncio.AbstractEventLoop] = None


def set_ws_loop(loop: asyncio.AbstractEventLoop):
    """设置 WebSocket 服务器的事件循环，用于跨线程调度"""
    global _ws_loop
    _ws_loop = loop
_current_task: Optional[threading.Thread] = None
_cancel_events: Dict[str, threading.Event] = {}
_running_prompt_futures: Dict[str, concurrent.futures.Future[Any]] = {}
_current_agent = "code_agent"


def _get_llm_client() -> tuple:
    """获取当前 LLM 客户端和配置"""
    llm_config = load_llm_config(_current_agent)
    model = llm_config.get("model", "deepseek-chat")
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url", "")
    if base_url.endswith("/chat/completions"):
        base_url = base_url.rsplit("/chat/completions", 1)[0]
    if not api_key or not base_url:
        # 回退到 provider 配置
        try:
            data = json.loads(open(_PROVIDERS_PATH, "r", encoding="utf-8").read())
            providers = data.get("providers", [])
            idx = data.get("activeProviderIndex", 0)
            if providers and 0 <= idx < len(providers):
                prov = providers[idx]
                api_key = prov.get("apiKey", api_key)
                base_url = prov.get("baseUrl", base_url)
                if base_url.endswith("/chat/completions"):
                    base_url = base_url.rsplit("/chat/completions", 1)[0]
                model = (prov.get("models") or [model])[0]
        except Exception:
            pass
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    return client, model


def _read_providers() -> dict:
    # 先读用户数据目录，再回退到打包根
    for p in [_PROVIDERS_PATH, _BUNDLED_PROVIDERS_PATH]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[Server] Failed to read llm_providers.json from {p}: {e}")
    return {"providers": [], "activeProviderIndex": 0}


def _write_providers(data: dict):
    with open(_PROVIDERS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _norm_project_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _validate_project_dir(path: str) -> tuple:
    """
    校验项目目录：
    必须存在、是目录、且非盘根（盘根会使所有路径边界检查失效）。
    Returns: (ok, error, resolved_path)
    """
    if not path:
        return False, "path required", ""
    resolved = os.path.realpath(os.path.expandvars(os.path.expanduser(path)))
    if not os.path.isdir(resolved):
        return False, f"not a directory: {path}", ""
    _drive, tail = os.path.splitdrive(resolved)
    if not tail or tail in ("\\", "/"):
        return False, "drive root is not a valid project path", ""
    return True, "", resolved


def _read_projects() -> dict:
    """读取项目列表 {active, projects[]}；文件不存在时用当前服务项目路径初始化"""
    try:
        if _PROJECTS_PATH.exists():
            with open(_PROJECTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("projects"), list):
                    projects = [_norm_project_path(p) for p in data["projects"] if p]
                    active = data.get("active", "")
                    if active not in projects and projects:
                        active = projects[0]
                    return {"active": active, "projects": projects}
    except Exception as e:
        logger.warning(f"[Server] Failed to read projects.json: {e}")
    svc = _ensure_service()
    cur = getattr(svc, "_current_project_path", None) or str(_PROJECT_ROOT)
    return {"active": cur, "projects": [_norm_project_path(cur)]}


def _write_projects(data: dict):
    with open(_PROJECTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_agent_model_config() -> dict:
    for p in [_AGENT_MODEL_PATH, _BUNDLED_AGENT_MODEL_PATH]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
    return {"version": "1.0", "agents": {}}


def _write_agent_model_config(data: dict):
    with open(_AGENT_MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _ensure_service() -> AgentChatServiceV2:
    global _current_service
    if _current_service is None:
        _current_service = AgentChatServiceV2()
    return _current_service


def _submit_clarify_answer(answer: str, request_id: str = "", answers: dict = None):
    """提交用户对 ask_question 的回答到 QuestionManager

    多问题支持：
    - 新通道：前端传 answers 对象 {问题文本: 答案}（多问题卡片），直接透传；
    - 旧通道：仅 answer 字符串（单问题 / skip / 旧前端），包装为 {"0": answer}。

    幂等护栏：仅接受"当前待答问题"的 request_id；无待答问题或 id 不匹配时
    直接忽略（旧卡重复提交不得污染下一次 ask 的等待事件）。
    """
    try:
        from tools.ask_question_tool import QuestionManager
        pending = QuestionManager.get_pending_questions()
        if not pending:
            logger.info(f"[Gateway] clarify.respond ignored (no pending question), request_id={request_id}")
            return
        pending_id = pending.get("id", "")
        if request_id and pending_id and request_id != pending_id:
            logger.warning(f"[Gateway] clarify.respond ignored (request_id mismatch: {request_id} != pending {pending_id})")
            return
        if answers:
            # 新通道：完整答案对象（键=问题文本，自描述，agent 可精确对应）
            QuestionManager.submit_answers({"answers": answers})
        else:
            # 旧通道：单答案字符串（兼容单问题 / skip）
            QuestionManager.submit_answers({"answers": {"0": answer}})
    except Exception as e:
        logger.warning(f"[Gateway] Failed to submit clarify answer: {e}")


def handle_request(method: str, params: dict, rid: str) -> Optional[dict]:
    global _current_agent
    llm_config = load_llm_config(_current_agent)
    active_model = llm_config.get("model", "deepseek-chat")
    if method == "session.create":
        project_path = params.get("project_path", "")
        session = _session_store.create(project_path=project_path)
        if project_path:
            _session_project_paths[session["session_id"]] = _norm_project_path(project_path)
        return {
        "id": rid,
        "result": {
        "session_id": session["session_id"],
        "info": {
        "model": active_model,
        "skills": load_skills_info(),
        "tools": load_tools_info(),
        }
        }
        }

    if method == "session.list":
        limit = params.get("limit", 200)
        project_path = params.get("project_path") or None
        sessions = _session_store.list(limit, project_path=project_path)
        return {"id": rid, "result": {"sessions": sessions}}

    if method == "session.most_recent":
        recent = _session_store.most_recent()
        return {"id": rid, "result": recent or {}}

    if method == "project.list":
        data = _read_projects()
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.add":
        path = _norm_project_path(params.get("path", ""))
        if not path:
            return {"id": rid, "error": {"code": -32000, "message": "path required"}}
        ok, err, resolved = _validate_project_dir(path)
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": err}}
        path = resolved
        data = _read_projects()
        if path not in data["projects"]:
            data["projects"].append(path)
            if not data["active"]:
                data["active"] = path
            _write_projects(data)
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.delete":
        path = _norm_project_path(params.get("path", ""))
        data = _read_projects()
        if path in data["projects"]:
            data["projects"].remove(path)
            if data["active"] == path:
                data["active"] = data["projects"][0] if data["projects"] else ""
            _write_projects(data)
        return {"id": rid, "result": {"projects": data["projects"], "active": data["active"]}}

    if method == "project.set_active":
        path = _norm_project_path(params.get("path", ""))
        data = _read_projects()
        if path in data["projects"]:
            # 校验：历史列表可能含盘根/失效路径，切换时复核
            ok, err, resolved = _validate_project_dir(path)
            if not ok:
                return {"id": rid, "error": {"code": -32000, "message": err}}
            data["active"] = resolved
            _write_projects(data)
            try:
                _ensure_service().set_project_path(resolved)
            except Exception as e:
                logger.warning(f"[Server] project.set_active service sync failed: {e}")
            return {"id": rid, "result": {"ok": True, "active": resolved}}
        return {"id": rid, "error": {"code": -32000, "message": "project not in list"}}

    if method == "session.unassigned_count":
        return {"id": rid, "result": {"count": _session_store.unassigned_count()}}

    if method == "session.adopt_unassigned":
        project_path = _norm_project_path(params.get("project_path", ""))
        if not project_path:
            return {"id": rid, "error": {"code": -32000, "message": "project_path required"}}
        adopted = _session_store.adopt_unassigned(project_path)
        return {"id": rid, "result": {"adopted": adopted}}

    if method == "session.resume":
        sid = params.get("session_id", "")
        session = _session_store.get(sid)
        if not session:
            return {"id": rid, "error": {"code": -32000, "message": f"Session not found: {sid}"}}
        # 回填内存项目映射（重启后 agent 工具路径不丢）
        spath = session.get("project_path") or ""
        if spath:
            _session_project_paths[sid] = _norm_project_path(spath)
        else:
            _session_project_paths.pop(sid, None)
        messages = _session_store.get_messages(sid, limit=999999)
        cost_summary = _session_store.get_cost_summary(sid)
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
        "info": {
        "model": active_model,
        "skills": load_skills_info(),
        "tools": load_tools_info(),
        }
        }
        }

    if method == "session.title":
        sid = params.get("session_id", "")
        title = params.get("title", "")
        if sid and title:
            _session_store.update_title(sid, title)
        return {"id": rid, "result": {"ok": True}}

    if method == "session.delete":
        sid = params.get("session_id", "")
        # 先取消该会话运行中的 agent：避免删除后 agent 继续写库
        # （曾导致：FK 崩溃 + agent_chat.db 孤儿消息 + 连接留锁连锁 "database is locked"）
        _ce = _cancel_events.get(sid)
        if _ce is not None:
            _ce.set()
            logger.info(f"[Server] Cancelled running agent before deleting session {sid}")
        ok = _session_store.delete(sid)
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
        session = _session_store.get(sid)
        if session:
            return {
            "id": rid,
            "result": {
            "session_id": sid,
            "info": {
            "title": session.get("title", ""),
            "message_count": session.get("message_count", 0),
            "model": active_model,
            "skills": load_skills_info(),
            "tools": load_tools_info(),
            }
            }
            }
        return {"id": rid, "result": {}}

    if method == "session.search":
        q = params.get("q", "").strip()
        limit = params.get("limit", 20)
        if not q:
            return {"id": rid, "result": {"results": [], "total": 0}}
        results = _session_store.search_messages(q, limit)
        return {
        "id": rid,
        "result": {"results": results, "total": len(results)},
        }

    if method == "session.export":
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        md = _session_store.export_markdown(sid)
        if md is None:
            return {"id": rid, "error": {"code": -32000, "message": "Session not found or empty"}}
        return {"id": rid, "result": {"markdown": md, "session_id": sid}}

    if method == "session.export_save":
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        md = _session_store.export_markdown(sid)
        if md is None:
            return {"id": rid, "error": {"code": -32000, "message": "Session not found or empty"}}
        session = _session_store.get(sid)
        title = (session or {}).get("title") or "New conversation"
        safe_title = "".join(c if c.isalnum() or c in " _-." else "_" for c in title)[:60]
        project_path = getattr(_ensure_service(), '_current_project_path', None) or str(_PROJECT_ROOT)
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
        raw_messages = _session_store.get_messages_for_context(sid, limit=999999)
        if not raw_messages:
            return {"id": rid, "result": {"success": False, "reason": "no messages"}}
        try:
            from agent_core.context_compressor import manual_compress
            client, model = _get_llm_client()

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
            recent_messages=10,
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

                    # sessions.db: 插入 marker，不删除旧消息
                    saved = _session_store.insert_compaction_result(sid, boundary, summary, kept_count)
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
                            ok = history.insert_compaction_result(conv["id"], boundary, summary)
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

    if method == "message.delete":
        """删除单条消息（同步删除 sessions.db 和 agent_chat.db）"""
        sid = params.get("session_id", "")
        message_id = params.get("message_id", 0)
        if not sid or not message_id:
            return {"id": rid, "error": {"code": -32000, "message": "session_id and message_id required"}}
        result = _session_store.delete_message(sid, message_id)
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

    if method == "model.options":
        data = _read_providers()
        providers_raw = data.get("providers", [])
        active_idx = data.get("activeProviderIndex", 0)
        active_provider = providers_raw[active_idx] if providers_raw and active_idx < len(providers_raw) else None
        providers_out = []
        for i, p in enumerate(providers_raw):
            providers_out.append({
            "name": p.get("provider", f"Provider {i}"),
            "slug": p.get("id", f"p{i}"),
            "models": p.get("models", []),
            "authenticated": bool(p.get("apiKey")),
            "is_current": i == active_idx,
            "apiKey": p.get("apiKey", ""),
            "baseUrl": p.get("baseUrl", ""),
            "temperature": p.get("temperature"),
            "topP": p.get("topP"),
            "reasoning_effort": p.get("reasoning_effort"),
            "thinking_enabled": p.get("thinking_enabled") is not False,
            "max_tokens": p.get("maxTokens", 4096),
            })
        current_model = active_provider.get("models", [None])[0] if active_provider else None
        agent_cfg = _read_agent_model_config().get("agents", {}).get(_current_agent, {})
        custom_model = agent_cfg.get("custom_model") or agent_cfg.get("model_source")
        if custom_model and custom_model not in ("inherit", "custom"):
            current_model = custom_model
        return {
        "id": rid,
        "result": {
        "model": current_model or "",
        "provider": active_provider.get("provider", "") if active_provider else "",
        "providers": providers_out,
        "activeProviderIndex": active_idx,
        }
        }

    if method == "model.provider.set":
        idx = params.get("index", 0)
        model = params.get("model", "")
        data = _read_providers()
        providers = data.get("providers", [])
        if 0 <= idx < len(providers):
            data["activeProviderIndex"] = idx
            _write_providers(data)
            if model and model in providers[idx].get("models", []):
                agent_cfg = _read_agent_model_config()
                agent_cfg.setdefault("agents", {})[_current_agent] = {
                "model_source": model,
                "custom_model": None,
                "temperature": None,
                "max_tokens": None,
                }
                _write_agent_model_config(agent_cfg)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.provider.save":
        idx = params.get("index", -1)
        provider_data = params.get("provider", {})
        data = _read_providers()
        providers = data.get("providers", [])
        new_provider = {
        "id": provider_data.get("slug", str(uuid.uuid4())),
        "provider": provider_data.get("name", "New Provider"),
        "apiKey": provider_data.get("apiKey", ""),
        "baseUrl": provider_data.get("baseUrl", ""),
        "models": provider_data.get("models", []),
        "temperature": _num(provider_data.get("temperature")),
        "maxTokens": _num(provider_data.get("maxTokens")) or 4096,
        "topP": _num(provider_data.get("topP")),
        "reasoning_effort": _str(provider_data.get("reasoning_effort")),
        "thinking_enabled": _bool(provider_data.get("thinking_enabled"), True),
        }
        if idx >= 0 and idx < len(providers):
            providers[idx] = new_provider
        else:
            providers.append(new_provider)
            data["activeProviderIndex"] = len(providers) - 1
        data["providers"] = providers
        _write_providers(data)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.provider.delete":
        idx = params.get("index", -1)
        data = _read_providers()
        providers = data.get("providers", [])
        if 0 <= idx < len(providers):
            providers.pop(idx)
            if data.get("activeProviderIndex", 0) >= len(providers):
                data["activeProviderIndex"] = max(0, len(providers) - 1)
            data["providers"] = providers
            _write_providers(data)
        return {"id": rid, "result": {"ok": True}}

    if method == "model.params.set":
        temperature = params.get("temperature")
        top_p = params.get("topP")
        reasoning_effort = params.get("reasoning_effort")
        thinking_enabled = _bool(params.get("thinking_enabled"))
        data = _read_providers()
        idx = data.get("activeProviderIndex", 0)
        providers = data.get("providers", [])
        if 0 <= idx < len(providers):
            if temperature is not None:
                providers[idx]["temperature"] = temperature
            if top_p is not None:
                providers[idx]["topP"] = top_p
            if reasoning_effort is not None:
                providers[idx]["reasoning_effort"] = reasoning_effort
            if thinking_enabled is not None:
                providers[idx]["thinking_enabled"] = thinking_enabled
            data["providers"] = providers
            _write_providers(data)
        return {"id": rid, "result": {"ok": True}}

    if method == "setup.status":
        llm_config = load_llm_config(_current_agent)
        configured = bool(llm_config.get("api_key"))
        return {"id": rid, "result": {"provider_configured": configured}}

    if method == "session.interrupt":
        sid = params.get("session_id", "")
        target_sids = [sid] if sid else list(_cancel_events.keys() | _running_prompt_futures.keys())
        for target_sid in target_sids:
            event = _cancel_events.get(target_sid)
            if event:
                event.set()

            # 真正取消正在 await LLM stream / tool 的 asyncio task，而不是只等下一块输出。
            future = _running_prompt_futures.get(target_sid)
            if future and not future.done():
                future.cancel()

            try:
                _ensure_service().cancel_chat(target_sid)
            except Exception as e:
                logger.warning(f"[Server] cancel_chat failed: {e}")
        return {"id": rid, "result": {"ok": True}}

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

    if method == "path_whitelist.read":
        whitelist_path = _DATA_DIR / "path_whitelist.json"
        bundled_whitelist = _PROJECT_ROOT / "path_whitelist.json"
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
        whitelist_path = _DATA_DIR / "path_whitelist.json"
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
                from agent_core.tool_executor_impl import PermissionEnforcer
                svc = _ensure_service()
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
            return {"id": rid, "result": {"mode": "default"}}

    if method == "permission.mode.set":
        """设置权限模式"""
        try:
            from gateway.permission_manager import get_permission_manager, PermissionMode
            mode_str = params.get("mode", "default")
            mode = PermissionMode(mode_str)
            mgr = get_permission_manager()
            mgr.set_mode(mode)
            # 模式切换写审计（bypass/autoAllow 等高危切换可追溯）
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
            return {"id": rid, "result": {"ok": True}}
        except Exception as e:
            logger.warning(f"[Server] permission.decide error: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

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

    if method == "config.get":
        key = params.get("key", "")
        if key == "full":
            return {
            "id": rid,
            "result": {
            "config": {
            "display": {
            "tui_auto_resume_recent": True,
            }
            }
            }
            }
        if key == "agent_stats":
            tools_info = load_tools_info()
            skills_info = load_skills_info()
            tool_count = sum(len(v) for v in tools_info.values())
            skill_count = sum(len(v) for v in skills_info.values())
            return {
            "id": rid,
            "result": {
            "tool_count": tool_count,
            "skill_count": skill_count,
            }
            }
        if key == "pricing":
            # 计费面板数据：model_pricing.json 全量价表 + 后端权威时段判定 + 法定节假日数据。
            # 前端本地轮询判断时段仅为展示预判，实际计费以后端 period 为准（单一权威）；
            # cn_holidays 与后端判定同源（cn_holidays.json），供前端本地预判同步排除法定节假日。
            pricing_data = None
            for p in [_DATA_DIR / "model_pricing.json", _PROJECT_ROOT / "model_pricing.json"]:
                if p.exists():
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            pricing_data = json.load(f)
                        break
                    except Exception:
                        continue
            pricing_data = pricing_data or {"models": {}, "default_currency": "CNY"}
            return {
            "id": rid,
            "result": {
            "pricing": pricing_data,
            "period": current_pricing_period(),  # 后端权威：'peak'（高峰）/ 'offpeak'（空闲）
            "cn_holidays": get_cn_holidays_payload(),  # 法定节假日数据（前端本地预判用，与后端判定同源）
            "timezone": "Asia/Shanghai (UTC+8)",
            }
            }
        return {"id": rid, "result": {}}

    if method == "config.set":
        return {"id": rid, "result": {"ok": True}}

    if method == "model.switch":
        model = params.get("model", "")
        data = _read_providers()
        providers = data.get("providers", [])
        idx = data.get("activeProviderIndex", 0)
        if 0 <= idx < len(providers) and model in providers[idx].get("models", []):
            agent_cfg = _read_agent_model_config()
            agent_cfg.setdefault("agents", {})[_current_agent] = {
            "model_source": model,
            "custom_model": None,
            "temperature": None,
            "max_tokens": None,
            }
            _write_agent_model_config(agent_cfg)
        return {"id": rid, "result": {"ok": True}}

    if method == "pricing.save":
        # 模型价格编辑面板：严格校验后原子写入数据目录 model_pricing.json
        # （mtime 热重载自动生效，无需重启；数据目录优先于打包根，打包版同样安全）
        models = params.get("models")
        if not isinstance(models, dict) or not models:
            return {"id": rid, "result": {"ok": False, "error": "models must be a non-empty object"}}
        clean_models = {}
        for name, entry in models.items():
            if not isinstance(name, str) or not name.strip():
                return {"id": rid, "result": {"ok": False, "error": "invalid model name"}}
            if not isinstance(entry, dict):
                return {"id": rid, "result": {"ok": False, "error": f"{name}: entry must be an object"}}
            ok, err = _validate_pricing_block(entry.get("pricing"))
            if not ok:
                return {"id": rid, "result": {"ok": False, "error": f"{name}: {err}"}}
            clean = dict(entry)
            clean["pricing"] = {k: float(entry["pricing"][k]) for k in _PRICING_KEYS}
            for bk in ("pricing_peak", "pricing_offpeak", "pricing_tier_high"):
                blk = entry.get(bk)
                if blk is None:
                    clean.pop(bk, None)
                    continue
                ok, err = _validate_pricing_block(blk)
                if not ok:
                    return {"id": rid, "result": {"ok": False, "error": f"{name}.{bk}: {err}"}}
                clean[bk] = {k: float(blk[k]) for k in _PRICING_KEYS}
            clean_models[name.strip()] = clean
        def_cur = params.get("default_currency")
        if not isinstance(def_cur, str) or not def_cur.strip():
            def_cur = "CNY"
        try:
            _atomic_write_json(_DATA_DIR / "model_pricing.json", {"models": clean_models, "default_currency": def_cur.strip()})
        except Exception as e:
            logger.error(f"[Server] Failed to save model_pricing.json: {e}")
            return {"id": rid, "result": {"ok": False, "error": f"write failed: {e}"}}
        return {"id": rid, "result": {"ok": True, "count": len(clean_models)}}

    if method == "slash.exec":
        return {"id": rid, "result": {"output": "ok"}}

    if method == "command.dispatch":
        name = params.get("name", "")
        if name == "model":
            return {"id": rid, "result": {"type": "exec", "output": f"use /model to open config panel"}}
        return {"id": rid, "result": {"type": "exec", "output": f"unknown command: {name}"}}

    if method == "agent.list":
        agents = []
        try:
            raw = list_available_agents_from_yaml()
            for agent in raw:
                agents.append({
                "name": agent.get("name", ""),
                "description": agent.get("description", ""),
                "current": agent.get("name", "") == _current_agent,
                })
        except Exception as e:
            logger.warning(f"[Server] Failed to list agents: {e}")
            agents = [{"name": "code_agent", "description": "Code expert", "current": True}]
        return {"id": rid, "result": {"agents": agents}}

    if method == "agent.switch":
        name = params.get("name", "")
        available = []
        try:
            for a in list_available_agents_from_yaml():
                available.append(a.get("name", ""))
        except:
            pass
        if name not in available:
            return {"id": rid, "result": {"ok": False, "error": f"Unknown agent: {name}", "available": available}}
        _current_agent = name
        logger.info(f"[Server] Agent switched to: {name}")
        # Reload LLM config for the new agent
        llm_config = load_llm_config(_current_agent)
        return {"id": rid, "result": {"ok": True, "agent": name}}

    if method == "project.set_path":
        path = params.get("path", "")
        if not path:
            return {"id": rid, "result": {"ok": False, "error": "path is required"}}
        # 校验：必须为存在的目录，且拒绝盘根（否则全部路径边界检查失效）
        ok, err, resolved = _validate_project_dir(path)
        if not ok:
            return {"id": rid, "result": {"ok": False, "error": err}}
        service = _ensure_service()
        service.set_project_path(resolved)
        logger.info(f"[Server] Project path changed to: {resolved}")
        os.environ["PEBBLE_CWD"] = resolved
        os.environ["HERMES_CWD"] = resolved
        return {"id": rid, "result": {"ok": True, "path": resolved}}

    if method == "mcp.list":
        mcp_path = _DATA_DIR / ".mcp.json"
        bundled_mcp = _PROJECT_ROOT / ".mcp.json"
        servers = []
        tools_list = []
        config = None
        for mp in [mcp_path, bundled_mcp]:
            if mp.exists():
                try:
                    with open(mp, "r", encoding="utf-8") as f:
                        config = json.load(f)
                        break
                except Exception as e:
                    logger.warning(f"[Server] Failed to read .mcp.json from {mp}: {e}")
        if config:
            # 尝试从 MCP manager 获取真实状态（只读，不触发初始化）
            try:
                from agent_core.mcp.mcp_manager import get_mcp_manager
                mgr = get_mcp_manager()
                state = mgr.get_state() if mgr._initialized else None
                if state:
                    server_tools: dict = {}
                    for t in state.tools:
                        sn = t.server_name or "unknown"
                        if sn not in server_tools:
                            server_tools[sn] = []
                        server_tools[sn].append(t.name)
                    connected_map = {
                    c.name: {"type": c.type, "error": getattr(c, 'error', None)} for c in state.clients
                    }
                else:
                    server_tools = {}
                    connected_map = {}
            except Exception:
                server_tools = {}
                connected_map = {}

            for name, cfg in config.get("mcpServers", {}).items():
                st = server_tools.get(name, [])
                conn_data = connected_map.get(name, {})
                conn_type = conn_data.get("type", "") if isinstance(conn_data, dict) else conn_data
                servers.append({
                "name": name,
                "transport": cfg.get("command", "unknown"),
                "connected": conn_type == "connected",
                "tools": len(st),
                "tool_names": st,
                "error": conn_data.get("error") if isinstance(conn_data, dict) else None,
                })
            # 收集所有工具
            for sn, tnames in server_tools.items():
                for tn in tnames:
                    tools_list.append({"server": sn, "name": tn})
        # 未批准的项目级 .mcp.json（防"克隆陌生仓库即执行其定义的服务器"）
        try:
            from agent_core.mcp.mcp_config import get_pending_project_mcp
            pending = get_pending_project_mcp(str(_PROJECT_ROOT))
        except Exception:
            pending = []
        return {"id": rid, "result": {"servers": servers, "tools": tools_list, "pending_project_servers": pending}}

    if method == "mcp.reload":
        logger.info("[Server] MCP reload requested")
        return {"id": rid, "result": {"status": "ok", "message": "MCP config reloaded"}}

    if method == "mcp.trust_project":
        """批准项目级 .mcp.json（记录当前内容指纹），并热加载其中定义的服务器"""
        path = str(params.get("path") or "").strip()
        if not path:
            return {"id": rid, "error": {"code": -32602, "message": "path required"}}
        try:
            from agent_core.mcp.mcp_config import trust_project_mcp
            if not os.path.isfile(path):
                return {"id": rid, "result": {"ok": False, "error": f"file not found: {path}"}}
            if not trust_project_mcp(path):
                return {"id": rid, "result": {"ok": False, "error": "failed to record trust"}}
            # 热加载：刷新配置并重连新出现的服务器（与 mcp.install_server 相同模式）
            reconnected = []
            try:
                import asyncio as _asyncio
                from agent_core.mcp.mcp_config import get_mcp_config_manager
                from agent_core.mcp.mcp_manager import get_mcp_manager
                cfg_mgr = get_mcp_config_manager()
                cfg_mgr.set_project_path(str(_PROJECT_ROOT))
                mgr = get_mcp_manager()
                if mgr._initialized:
                    new_configs = cfg_mgr.get_all_configs()
                    for name, cfg in new_configs.items():
                        if name not in mgr._configs:
                            mgr._configs[name] = cfg
                            if _ws_loop:
                                _asyncio.run_coroutine_threadsafe(mgr.reconnect_server(name), _ws_loop)
                                reconnected.append(name)
            except Exception as e:
                logger.warning(f"[Server] mcp.trust_project hot reload failed: {e}")
            logger.info(f"[Server] mcp.trust_project approved: {path} (reconnected={reconnected})")
            return {"id": rid, "result": {"ok": True, "reconnected": reconnected}}
        except Exception as e:
            logger.error(f"[Server] mcp.trust_project failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "mcp.set_secret":
        """写入 MCP 密钥到用户数据目录 .mcp.json 并热生效（如 GitHub Token）。
        参数：server / env_key / value。写入策略：以现有用户文件（或内嵌文件）为
        基础复制结构，只更新目标 env 字段，保留其他服务器与字段。"""
        import re as _re
        server_name = str(params.get("server") or "").strip()
        env_key = str(params.get("env_key") or "").strip()
        value = str(params.get("value") or "").strip()
        if not server_name or not env_key or not value:
            return {"id": rid, "error": {"code": -32602, "message": "server, env_key and value are required"}}
        if not _re.fullmatch(r"[A-Za-z0-9_]+", env_key):
            return {"id": rid, "error": {"code": -32602, "message": "invalid env_key"}}
        user_mcp_path = _DATA_DIR / ".mcp.json"
        bundled_mcp = _PROJECT_ROOT / ".mcp.json"
        data = None
        for p in [user_mcp_path, bundled_mcp]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    break
                except Exception:
                    continue
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
            return {"id": rid, "error": {"code": -32603, "message": "base .mcp.json not found or invalid"}}
        srv = data["mcpServers"].get(server_name)
        if not isinstance(srv, dict):
            return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' not found"}}
        env = srv.get("env")
        if not isinstance(env, dict):
            env = {}
        env[env_key] = value
        srv["env"] = env
        _atomic_write_json(user_mcp_path, data)
        reconnected = False
        try:
            from agent_core.mcp.mcp_config import build_user_override_configs, get_mcp_config_manager
            from agent_core.mcp.mcp_manager import get_mcp_manager
            overrides = build_user_override_configs()
            if overrides:
                get_mcp_config_manager().set_dynamic_configs(overrides)
            mgr = get_mcp_manager()
            if mgr._initialized and server_name in overrides:
                mgr._configs[server_name] = overrides[server_name]
                if _ws_loop:
                    _fut = asyncio.run_coroutine_threadsafe(mgr.reconnect_server(server_name), _ws_loop)

                    def _on_reconnect_done(_done, _sn=server_name):
                        try:
                            _done.result()
                            logger.info(f"[Server] MCP '{_sn}' reconnected with updated secret")
                        except Exception as _e:
                            logger.warning(f"[Server] MCP '{_sn}' reconnect failed: {_e}")

                    _fut.add_done_callback(_on_reconnect_done)
                    reconnected = True
        except Exception as e:
            logger.warning(f"[Server] MCP secret hot-apply failed (applies on next restart): {e}")
        logger.info(f"[Server] MCP secret saved: {server_name}.{env_key} (value_len={len(value)})")
        return {"id": rid, "result": {"ok": True, "reconnected": reconnected}}

    if method == "mcp.secret_status":
        """查询 MCP 服务器密钥配置状态（脱敏：只返回是否已配置，绝不回显值）"""
        import re as _re
        secrets_map: dict = {}
        user_mcp_path = _DATA_DIR / ".mcp.json"
        bundled_mcp = _PROJECT_ROOT / ".mcp.json"
        for mp in [user_mcp_path, bundled_mcp]:
            if not mp.exists():
                continue
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            for name, cfg in (data.get("mcpServers") or {}).items():
                env = (cfg or {}).get("env") if isinstance(cfg, dict) else None
                if not isinstance(env, dict):
                    continue
                bucket = secrets_map.setdefault(name, {})
                for k, v in env.items():
                    if not isinstance(v, str):
                        continue
                    configured = bool(v.strip()) and not _re.search(r"x{4,}|YOUR_|_HERE|REPLACE_WITH", v)
                    bucket.setdefault(k, configured)
        return {"id": rid, "result": {"secrets": secrets_map}}

    if method == "mcp.install_server":
        """下载安装 download 类型 MCP 服务器（如 codebase-memory）并写入配置。
        自动模式：从 mcp_install.json 取条目 → GitHub API 解析最新 release 资产 → 下载 →
        安全解压到 install_dir → exe 路径写入 ~/.pebble/.mcp.json → 热重连。
        手动模式：参数 path 指定本地 exe 路径（跳过下载）。"""
        server_name = str(params.get("server") or "").strip()
        manual_path = str(params.get("path") or "").strip()
        # 1. 读 mcp_install.json 条目
        install_info = {}
        for p in [_DATA_DIR / "mcp_install.json", _PROJECT_ROOT / "mcp_install.json"]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        install_info = json.load(f)
                    break
                except Exception:
                    continue
        entry = (install_info.get("servers") or {}).get(server_name)
        if not isinstance(entry, dict) or entry.get("type") != "download":
            return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' is not a download-type server"}}
        # 2. 获取 exe 路径（手动或自动）
        if manual_path:
            exe_path = manual_path
            if not os.path.isfile(exe_path):
                return {"id": rid, "error": {"code": -32602, "message": f"file not found: {exe_path}"}}
        else:
            try:
                exe_path = _install_download_server(server_name, entry)
            except Exception as e:
                logger.error(f"[Server] MCP install failed for '{server_name}': {e}")
                return {"id": rid, "result": {"ok": False, "error": str(e)}}
        # 3. 合并写入 ~/.pebble/.mcp.json（保留其他服务器）
        try:
            user_mcp_path = _DATA_DIR / ".mcp.json"
            bundled_mcp = _PROJECT_ROOT / ".mcp.json"
            data = None
            for p in [user_mcp_path, bundled_mcp]:
                if p.exists():
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        break
                    except Exception:
                        continue
            if not isinstance(data, dict):
                data = {}
            if not isinstance(data.get("mcpServers"), dict):
                data["mcpServers"] = {}
            data["mcpServers"][server_name] = {"command": exe_path, "args": []}
            _atomic_write_json(user_mcp_path, data)
        except Exception as e:
            logger.error(f"[Server] Failed to write .mcp.json for '{server_name}': {e}")
            return {"id": rid, "result": {"ok": False, "error": f"config write failed: {e}"}}
        # 4. 热重连（非阻塞提交，同 set_secret 模式）
        reconnected = False
        try:
            from agent_core.mcp.mcp_config import build_user_override_configs, get_mcp_config_manager
            from agent_core.mcp.mcp_manager import get_mcp_manager
            overrides = build_user_override_configs()
            if overrides:
                get_mcp_config_manager().set_dynamic_configs(overrides)
            mgr = get_mcp_manager()
            if mgr._initialized and server_name in overrides:
                mgr._configs[server_name] = overrides[server_name]
                if _ws_loop:
                    _fut = asyncio.run_coroutine_threadsafe(mgr.reconnect_server(server_name), _ws_loop)

                    def _on_install_reconnect(_done, _sn=server_name):
                        try:
                            _done.result()
                            logger.info(f"[Server] MCP '{_sn}' reconnected after install")
                        except Exception as _e:
                            logger.warning(f"[Server] MCP '{_sn}' reconnect failed: {_e}")

                    _fut.add_done_callback(_on_install_reconnect)
                    reconnected = True
        except Exception as e:
            logger.warning(f"[Server] MCP install hot-apply failed (applies on next restart): {e}")
        logger.info(f"[Server] MCP server installed: {server_name} -> {exe_path}")
        return {"id": rid, "result": {"ok": True, "exe_path": exe_path, "reconnected": reconnected}}

    if method == "mcp.install_info":
        """检查 MCP 前置依赖安装状态，返回安装指南"""
        import shutil
        install_path = _DATA_DIR / "mcp_install.json"
        bundled_install = _PROJECT_ROOT / "mcp_install.json"
        info = {}
        for p in [install_path, bundled_install]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        info = json.load(f)
                        break
                except Exception:
                    pass
        prereqs = {}
        for name, cfg in info.get("prerequisites", {}).items():
            found = shutil.which(cfg.get("check_command", "").split()[0] if " " in cfg.get("check_command", "") else cfg["check_command"])
            prereqs[name] = {
            "installed": found is not None,
            "label": cfg.get("download_label", name),
            "url": cfg.get("download_url", ""),
            "description": cfg.get("description", ""),
            }
        servers = []
        for name, cfg in info.get("servers", {}).items():
            pkg = cfg.get("package", "")
            url = cfg.get("url", "")
            servers.append({
            "name": name,
            "type": cfg.get("type", "npx"),
            "package": pkg,
            "url": url,
            "description": cfg.get("description", ""),
            "requires": cfg.get("requires", []),
            })
        return {"id": rid, "result": {"prerequisites": prereqs, "servers": servers}}

    # ==================== Experience Ledger API ====================
    def _get_working_project():
        """获取当前工作项目路径"""
        service = _ensure_service()
        path = getattr(service, '_current_project_path', None)
        return path or str(_PROJECT_ROOT)

    def _ledger():
        from agent_core.memory import get_ledger
        instance = get_ledger()
        instance.set_project(_get_working_project())
        return instance

    def _serialize_experience(entry):
        def iso(ts):
            if not ts:
                return ""
            return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
        return {
            "id": entry["id"],
            "kind": entry["kind"],
            "statement": entry["statement"],
            "rationale": entry["rationale"],
            "applies_to": entry["applies_to"],
            "evidence": entry["evidence"],
            "confidence": entry["confidence"],
            "status": entry["status"],
            "hits": entry["hits"],
            "created_at": iso(entry["created_at"]),
            "updated_at": iso(entry["updated_at"]),
        }

    if method == "memory.list":
        kind = params.get("kind") or params.get("memory_type") or None
        status = params.get("status", "active")
        try:
            entries = _ledger().list(kind=kind, status=status if status != "all" else None)
            result = [_serialize_experience(e) for e in entries]
            return {"id": rid, "result": {"success": True, "memories": result, "count": len(result)}}
        except Exception as e:
            logger.error(f"[Server] memory.list failed: {e}")
            return {"id": rid, "result": {"success": False, "memories": [], "count": 0, "error": str(e)}}

    if method == "memory.stats":
        try:
            return {"id": rid, "result": {"success": True, **_ledger().stats()}}
        except Exception as e:
            logger.error(f"[Server] memory.stats failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "memory.get":
        exp_id = params.get("id", "")
        if not exp_id:
            return {"id": rid, "error": {"code": -32000, "message": "id required"}}
        try:
            entry = _ledger().get(exp_id)
            if entry:
                return {"id": rid, "result": {"success": True, "memory": _serialize_experience(entry)}}
            return {"id": rid, "result": {"success": False, "memory": None, "error": "not found"}}
        except Exception as e:
            logger.error(f"[Server] memory.get failed: {e}")
            return {"id": rid, "result": {"success": False, "memory": None, "error": str(e)}}

    if method == "memory.create":
        kind = params.get("kind", "preference")
        statement = (params.get("statement") or "").strip()
        rationale = params.get("rationale", "")
        applies_to = params.get("applies_to") or {}
        confidence = _num(params.get("confidence"))
        if not statement:
            return {"id": rid, "error": {"code": -32000, "message": "statement required"}}
        try:
            entry = _ledger().create(
                kind=kind,
                statement=statement,
                rationale=rationale,
                applies_to=applies_to if isinstance(applies_to, dict) else {},
                confidence=confidence if confidence is not None else 0.8,
            )
            if not entry:
                return {"id": rid, "result": {"success": False, "memory": None, "error": "invalid entry"}}
            return {"id": rid, "result": {"success": True, "memory": _serialize_experience(entry)}}
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
            fields["confidence"] = _num(params.get("confidence"))
        try:
            entry = _ledger().update(exp_id, **fields)
            if entry:
                return {"id": rid, "result": {"success": True, "memory": _serialize_experience(entry)}}
            return {"id": rid, "result": {"success": False, "memory": None, "error": "not found"}}
        except Exception as e:
            logger.error(f"[Server] memory.update failed: {e}")
            return {"id": rid, "result": {"success": False, "memory": None, "error": str(e)}}

    if method == "memory.delete":
        exp_id = params.get("id", "")
        if not exp_id:
            return {"id": rid, "error": {"code": -32000, "message": "id required"}}
        try:
            return {"id": rid, "result": {"success": _ledger().delete(exp_id)}}
        except Exception as e:
            logger.error(f"[Server] memory.delete failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "memory.prompt":
        agent_name = params.get("agent_name", None)
        query = params.get("query", "")
        try:
            prompt = _ledger().build_prompt(query=query, agent_type=agent_name)
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
            content = _ledger().get_agent_note(agent_type, scope)
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
            return {"id": rid, "result": {"success": _ledger().set_agent_note(agent_type, scope, content)}}
        except Exception as e:
            logger.error(f"[Server] agent-memory.set failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "agent-memory.clear":
        agent_type = params.get("agent_type", "")
        scope = params.get("scope", "project")
        if not agent_type:
            return {"id": rid, "error": {"code": -32000, "message": "agent_type required"}}
        try:
            return {"id": rid, "result": {"success": _ledger().clear_agent_note(agent_type, scope)}}
        except Exception as e:
            logger.error(f"[Server] agent-memory.clear failed: {e}")
            return {"id": rid, "result": {"success": False, "error": str(e)}}

    if method == "clarify.respond":
        answer = params.get("answer", "")
        request_id = params.get("request_id", "")
        answers = params.get("answers") or None  # 新：多问题结构化答案 {问题文本: 答案}
        _submit_clarify_answer(answer, request_id, answers)
        return {"id": rid, "result": {"ok": True}}

    if method == "file.search":
        """搜索项目目录中的文件（自动补全用；目录受项目边界/白名单限制）"""
        q = params.get("q", "").strip().lower()
        path = params.get("path", str(_PROJECT_ROOT))
        try:
            limit = max(1, min(int(params.get("limit", 20)), 1000))
        except Exception:
            limit = 20
        ok, err, resolved = _guard_rpc_path(path)
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
        ok, err, resolved = _guard_rpc_path(path)
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
        ok, err, resolved = _guard_rpc_path(path)
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
        ok, err, resolved = _guard_rpc_path(path)
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
        ok, err, resolved = _guard_rpc_path(path, allow_url=True)
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

    # ==================== 用户便签 (usernote) ====================
    _USERRNOTE_PATH = _DATA_DIR / "usernote.json"
    _BUNDLED_USERRNOTE_PATH = _PROJECT_ROOT / "usernote.json"

    def _load_usernote() -> str:
        try:
            for p in [_USERRNOTE_PATH, _BUNDLED_USERRNOTE_PATH]:
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return data.get("content", "")
            return ""
        except Exception:
            return ""

    def _save_usernote(content: str) -> None:
        from datetime import datetime
        with open(_USERRNOTE_PATH, "w", encoding="utf-8") as f:
            json.dump({"content": content, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False)

    if method == "usernote.read":
        return {"id": rid, "result": {"content": _load_usernote()}}

    if method == "usernote.write":
        content = params.get("content", "")
        _save_usernote(content)
        return {"id": rid, "result": {"success": True, "content": content}}

    # ==================== 快捷备注 (quick notes) ====================
    # 与 usernote 同构：后端文件持久化（~/.pebble/quick_notes.json）。
    # 历史版本 QuickNotes 用 localStorage 存储，受浏览器端口漂移/数据清理影响
    # 会丢失（用户报告：重启后备注消失）。后端文件存储跨模式/端口稳定。
    _QUICK_NOTES_PATH = _DATA_DIR / "quick_notes.json"
    _BUNDLED_QUICK_NOTES_PATH = _PROJECT_ROOT / "quick_notes.json"

    def _load_quick_notes() -> list:
        try:
            for p in [_QUICK_NOTES_PATH, _BUNDLED_QUICK_NOTES_PATH]:
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        notes = data.get("notes", []) if isinstance(data, dict) else data
                        if isinstance(notes, list):
                            return notes
            return []
        except Exception:
            return []

    def _save_quick_notes(notes: list) -> None:
        from datetime import datetime
        with open(_QUICK_NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump({"notes": notes, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False)

    if method == "quick-notes.read":
        return {"id": rid, "result": {"notes": _load_quick_notes()}}

    if method == "quick-notes.write":
        notes = params.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        _save_quick_notes(notes)
        return {"id": rid, "result": {"success": True, "notes": notes}}

    # ==================== 文件变更面板 (File Changes) ====================
    if method == "session.file_changes":
        """获取当前会话的所有文件变更列表"""
        sid = params.get("session_id", "")
        if not sid:
            return {"id": rid, "error": {"code": -32000, "message": "session_id required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            from agent_core.snapshot.snapshot_diff import DiffEngine
            project_path = params.get("project_path", "") or _session_project_paths.get(sid, str(_PROJECT_ROOT))
            if params.get("project_path"):
                _session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "result": {"files": [], "total_files": 0, "total_additions": 0, "total_deletions": 0}}
            records = manager.query_all_active()
            if not records:
                return {"id": rid, "result": {"files": [], "total_files": 0, "total_additions": 0, "total_deletions": 0}}
            actual_project = getattr(manager, 'project_path', project_path)
            diff_result = DiffEngine.compute_diff(records, actual_project)
            files_out = []
            total_add = 0
            total_del = 0
            for ch in diff_result.get('changes', []):
                add_count = sum(1 for h in ch.get('hunks', []) for l in h.get('lines', []) if l.get('prefix') == '+')
                del_count = sum(1 for h in ch.get('hunks', []) for l in h.get('lines', []) if l.get('prefix') == '-')
                total_add += add_count
                total_del += del_count
                files_out.append({
                "relative_path": ch['file'],
                "status": ch['status'],
                "message": ch.get('message', ''),
                "added_lines": add_count,
                "removed_lines": del_count,
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
            project_path = params.get("project_path", "") or _session_project_paths.get(sid, str(_PROJECT_ROOT))
            if params.get("project_path"):
                _session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "result": {"accepted": 0}}
            accepted = manager.accept_all_active()
            logger.info(f"[Server] Accepted {accepted} file changes for session {sid}")
            return {"id": rid, "result": {"accepted": accepted}}
        except Exception as e:
            logger.error(f"[Server] session.file_accept failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    # ── 辅助：路径归一化（统一正斜杠、去除首尾空格） ──
    def _norm_path(p: str) -> str:
        return p.strip().replace('\\', '/').lower()

    if method == "session.file_diff":
        """获取某个文件的详细 diff"""
        sid = params.get("session_id", "")
        raw_path = params.get("relative_path", "")
        if not sid or not raw_path:
            return {"id": rid, "error": {"code": -32000, "message": "session_id and relative_path required"}}
        try:
            from agent_core.snapshot.snapshot_manager import get_snapshot_manager
            from agent_core.snapshot.snapshot_diff import DiffEngine
            project_path = params.get("project_path", "") or _session_project_paths.get(sid, str(_PROJECT_ROOT))
            if params.get("project_path"):
                _session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "result": {"file": raw_path, "status": "error", "hunks": [], "message": "Snapshot manager not available"}}
            # 使用 manager 实例的 project_path，保证与快照记录一致
            actual_project = getattr(manager, 'project_path', project_path)
            # 取所有活跃记录，用归一化路径匹配
            all_records = manager.query_all_active()
            target_path = _norm_path(raw_path)
            matched = []
            for r in all_records:
                nr = _norm_path(r.relative_path)
                na = _norm_path(r.absolute_path)
                if (nr == target_path or na == target_path or
                    target_path in nr or nr in target_path or
                    os.path.basename(nr) == os.path.basename(target_path)):
                    matched.append(r)
            if not matched:
                return {"id": rid, "result": {"file": raw_path, "status": "noop", "hunks": [], "message": "No snapshots found for this file"}}
            diff_result = DiffEngine.compute_diff(matched, actual_project)
            for ch in diff_result.get('changes', []):
                norm_file = _norm_path(ch['file'])
                norm_basename = os.path.basename(norm_file) if norm_file else ''
                if (norm_file == target_path or target_path in norm_file or
                        norm_file in target_path or norm_basename == os.path.basename(target_path)):
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
            project_path = params.get("project_path", "") or _session_project_paths.get(sid, str(_PROJECT_ROOT))
            if params.get("project_path"):
                _session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "error": {"code": -32000, "message": "Snapshot manager not available"}}
            all_records = manager.query_all_active()
            target_path = _norm_path(raw_path)
            records = [r for r in all_records if _norm_path(r.relative_path) == target_path or
            _norm_path(r.absolute_path) == target_path or
            target_path in _norm_path(r.relative_path)]
            target = None
            for r in records:
                if r.revert_status == 'active':
                    target = r
                    break
            if not target:
                return {"id": rid, "result": {"success": False, "message": "已撤销或找不到快照记录"}}
            executor = RevertExecutor(manager)
            manager.mark_records_as_reverting([target.record_id])
            result = executor._restore_single_file(target)
            if result.status == "success":
                manager.mark_records_as_reverted([target.record_id], sid + "_manual")
                # 统计该文件剩余活跃快照数
                remaining = sum(1 for r in records if r.record_id != target.record_id and r.revert_status == 'active')
                return {"id": rid, "result": {"success": True, "message": result.message, "remaining": remaining}}
            else:
                return {"id": rid, "result": {"success": False, "message": result.message}}
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
            project_path = params.get("project_path", "") or _session_project_paths.get(sid, str(_PROJECT_ROOT))
            if params.get("project_path"):
                _session_project_paths[sid] = os.path.abspath(params["project_path"])
            manager = get_snapshot_manager(sid, project_path)
            if not manager:
                return {"id": rid, "error": {"code": -32000, "message": "Snapshot manager not available"}}
            executor = RevertExecutor(manager)
            # 直接执行回退（同步模式）
            records = manager.query_by_message(message_id)
            if not records:
                return {"id": rid, "result": {"success": False, "message": "No snapshots found for this message"}}
            # 去重
            seen = {}
            for r in records:
                if r.relative_path not in seen:
                    seen[r.relative_path] = r
            unique = list(seen.values())
            restored = 0
            failed = 0
            for record in unique:
                manager.mark_records_as_reverting([record.record_id])
                res = executor._restore_single_file(record)
                if res.status == "success":
                    manager.mark_records_as_reverted([record.record_id], sid + "_batch")
                    restored += 1
                else:
                    failed += 1
            return {"id": rid, "result": {
            "success": failed == 0,
            "restored": restored,
            "failed": failed,
            "message": f"Restored {restored} files, {failed} failed"
            }}
        except Exception as e:
            logger.error(f"[Server] session.file_revert_all failed: {e}")
            return {"id": rid, "error": {"code": -32000, "message": str(e)}}

    return None


def handle_prompt_submit(
    sid: str,
    text: str,
    write_event,
    attachments: list | None = None,
    project_path: str | None = None,
) -> None:

    cancel_event = threading.Event()
    _cancel_events[sid] = cancel_event
    cancel_event.clear()

    # 记录项目路径，供快照查询 API 使用
    if project_path:
        _session_project_paths[sid] = os.path.abspath(project_path)

    llm_config = load_llm_config(_current_agent)
    service = _ensure_service()
    agent_name = _current_agent

    reasoning_effort = None
    thinking_enabled = True
    temperature = None
    top_p = None
    try:
        data = _read_providers()
        providers_list = data.get("providers", [])
        idx = data.get("activeProviderIndex", 0)
        if providers_list and 0 <= idx < len(providers_list):
            prov = providers_list[idx]
            re_val = prov.get("reasoning_effort")
            reasoning_effort = str(re_val).strip() if re_val else None
            thinking_enabled = prov.get("thinking_enabled") is not False
            temp_val = prov.get("temperature")
            if temp_val is not None:
                try: temperature = float(temp_val)
                except: pass
            tp_val = prov.get("topP")
            if tp_val is not None:
                try: top_p = float(tp_val)
                except: pass
    except Exception:
        pass

    prev_count = _session_store.message_count(sid)
    _session_store.increment_messages(sid)
    user_msg_id, user_msg_uuid = _session_store.add_user_message(sid, text)
    if prev_count == 0:
        _session_store.update_title(sid, text[:50] or "New conversation")

    async def run():
        try:
            write_event("user_message.saved", {"message_id": user_msg_id, "session_id": sid})
            gen = service.chat_stream(
                agent_name=agent_name, message=text, conversation_uuid=sid,
                model=llm_config.get("model", "gpt-4"), project_path=project_path,
                temperature=temperature, top_p=top_p,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
                attachments=attachments, user_msg_uuid=user_msg_uuid)
            full_content = ""
            full_reasoning = ""
            tool_executions = []
            was_interrupted = False
            chat_completed_data = None
            async for event in gen:
                if cancel_event.is_set():
                    logger.info(f"[Server] Chat cancelled by user for session {sid}")
                    was_interrupted = True
                event_type = event.get("type", "")
                if event_type == "content":
                    chunk = event.get("content", "")
                    full_content += chunk
                    write_event("message.delta", {"text": chunk})
                elif event_type == "reasoning":
                    chunk = event.get("content", "")
                    full_reasoning += chunk
                    write_event("thinking.delta", {"text": chunk})
                elif event_type == "tool_call_streaming":
                    tool_id = event.get("tool_call_id", "")
                    tool_name = event.get("tool_name", "")
                    args_full = event.get("arguments_full", "")
                    if tool_id:
                        write_event("tool.call_streaming", {"tool_id": tool_id, "name": tool_name, "args_text": args_full})
                elif event_type == "tool_execution":
                    tool_executions.append(event)
                    tool_name = event.get("tool_name", "")
                    status = event.get("status", "")
                    tool_id = event.get("tool_execution_id", "")
                    tool_input = event.get("input", "")
                    tool_output = event.get("output", "")
                    if status == "running":
                        write_event("tool.start", {"tool_id": tool_id, "name": tool_name, "args_text": tool_input})
                    elif status in ("completed", "failed"):
                        ctx = tool_name
                        if tool_input:
                            ctx += f"({tool_input[:80]})"
                        tcid = event.get("tool_call_id", "")
                        # 工具留痕失败（如会话已被删除导致 FK 失败）不应中断会话，
                        # 工具本身已执行完毕，前端事件照常发送。
                        try:
                            _session_store.add_tool_message(sid, tool_name, ctx, tool_call_id=tcid)
                        except Exception as _te:
                            logger.warning(f"[Server] Failed to save tool message for {sid}: {_te}")
                        write_event("tool.complete", {"tool_id": tool_id, "name": tool_name, "result_text": tool_output, "error": tool_output if status == "failed" else None, "duration_s": event.get("duration", 0) / 1000})
                        if tool_name in ("write_file", "edit_file", "delete_file", "move_file") and status == "completed":
                            try:
                                args = json.loads(tool_input) if tool_input else {}
                                file_path = (args.get("file_path") or args.get("path") or args.get("source") or "").replace("\\", "/")
                                if file_path:
                                    logger.info(f"[file.change] sid={sid} tool={tool_name} path={file_path!r}")
                                    write_event("file.change", {"relative_path": file_path, "operation_type": tool_name.replace("_file", ""), "tool_name": tool_name, "tool_id": tool_id})
                            except Exception as e:
                                logger.warning(f"[file.change] failed: {e}")
                elif event_type == "todo_update":
                    todos = event.get("todos", [])
                    if todos:
                        write_event("todos.update", {"todos": todos})
                elif event_type == "ask_question":
                    questions = event.get("questions", [])
                    if questions:
                        qs = questions if isinstance(questions, list) else [questions]
                        # 多问题支持：发送完整问题列表；单问题字段保留向后兼容
                        q_full = [{
                            "question": q.get("question", "") if isinstance(q, dict) else str(q),
                            "header": (q.get("header", "QUESTION") if isinstance(q, dict) else "QUESTION"),
                            "options": [c.get("label", str(c)) if isinstance(c, dict) else str(c) for c in (q.get("options", []) if isinstance(q, dict) else [])],
                        } for q in qs]
                        write_event("clarify.request", {
                            "request_id": event.get("question_id", str(uuid.uuid4())),
                            "question": q_full[0]["question"],  # 兼容旧前端
                            "choices": q_full[0]["options"],    # 兼容旧前端
                            "questions": q_full,                # 新：完整问题列表
                        })
                elif event_type == "cancelled":
                    logger.info(f"[Server] Agent cancelled for session {sid}")
                    was_interrupted = True
                elif event_type == "title_updated":
                    write_event("session.updated", {"title": event.get("title", ""), "session_id": sid})
                elif event_type == "usage":
                    write_event("message.usage", {"model": event.get("model", ""), "session_cost_cny": event.get("session_cost_cny", 0), "latest_input_tokens": event.get("latest_input_tokens", 0), "latest_output_tokens": event.get("latest_output_tokens", 0), "model_context_limit": event.get("model_context_limit", 0), "period": event.get("period"), "this_call": event.get("this_call")})
                elif event_type == "chat_completed":
                    chat_completed_data = event
                    break
                elif event_type == "error":
                    err_msg = event.get("message", "Unknown error")
                    # 持久化错误消息（刷新/重启后仍可见）
                    try:
                        _session_store.add_assistant_message(sid, f"**Error:** {err_msg}", None, None, interrupted=True)
                    except Exception as _se:
                        logger.warning(f"[Server] Failed to save error message: {_se}")
                    write_event("error", {"message": err_msg})
                    break
            if chat_completed_data:
                # 保存完整 execution_order（含 reasoning/text/tool 交错顺序），
                # 供刷新恢复时前端按真实输出顺序渲染；tool_executions 仅作兜底。
                # msg_uuid 必须用 agent 侧生成的 assistant_msg_uuid，保证与 agent_chat.db 一致，
                # 否则 message.delete 时按 uuid 同步删除 agent_chat.db 必然失败（假删除）。
                full_eo = chat_completed_data.get("execution_order") or tool_executions
                # 保存失败（如会话已被删除导致 FK 失败）不应把"已成功完成的对话"变成 Chat error；
                # 前端已通过事件收到完整回复，此处仅持久化留痕，失败记 warning 即可。
                try:
                    _session_store.add_assistant_message(sid, full_content, full_reasoning, json.dumps(full_eo) if full_eo else None, msg_uuid=chat_completed_data.get("assistant_msg_uuid") or "", interrupted=was_interrupted)
                except Exception as _se:
                    logger.warning(f"[Server] Failed to save assistant message for {sid}: {_se}")
                write_event("message.complete", {"text": full_content, "reasoning": full_reasoning, "tool_calls": json.dumps(tool_executions), "interrupted": was_interrupted, "message_id": chat_completed_data.get("message_id"), "session_id": sid})
            else:
                try:
                    _session_store.add_assistant_message(sid, full_content, full_reasoning, json.dumps(tool_executions) if tool_executions else None, interrupted=was_interrupted)
                except Exception as _se:
                    logger.warning(f"[Server] Failed to save assistant message for {sid}: {_se}")
                write_event("message.complete", {"text": full_content, "reasoning": full_reasoning, "tool_calls": json.dumps(tool_executions), "interrupted": was_interrupted, "session_id": sid})
        except Exception as e:
            logger.error(f"[Server] Chat error: {e}")
            # 持久化错误消息（刷新/重启后仍可见）
            try:
                _session_store.add_assistant_message(sid, f"**Error:** {e}", None, None, interrupted=True)
            except Exception as _se:
                logger.warning(f"[Server] Failed to save error message: {_se}")
            write_event("error", {"message": str(e)})

    if _ws_loop:
        future = asyncio.run_coroutine_threadsafe(run(), _ws_loop)
        _running_prompt_futures[sid] = future

        def _cleanup(done_future: concurrent.futures.Future[Any]):
            current = _running_prompt_futures.get(sid)
            if current is done_future:
                _running_prompt_futures.pop(sid, None)

        future.add_done_callback(_cleanup)
    else:
        asyncio.run(run())
        _cancel_events.pop(sid, None)
