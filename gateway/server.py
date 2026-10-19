import asyncio
import concurrent.futures
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


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
                f"'{server_name}' has no release checksum configured; automatic install is unavailable. "
                f"Download the release manually and use Set path, or add its sha256 to mcp_install.json."
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


# Loaded here and reached by gateway/rpc handlers as srv.<name>.
from .config import load_llm_config, load_tools_info, load_skills_info  # noqa: F401
from .session import SessionStore
from .session_title import generate_session_title
from .rpc import RpcContext, dispatch

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Also reached by gateway/rpc handlers as srv.<name>.
from agent_core.agent_chat_service_v2 import AgentChatServiceV2, current_pricing_period, get_cn_holidays_payload  # noqa: F401
from agent_core.agent_loader import list_available_agents_from_yaml  # noqa: F401

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
        except Exception as e:
            logger.warning(f"[Server] Failed to load llm_providers.json overrides: {e}")
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
            except Exception as e:
                logger.warning(f"[Server] Failed to read agent model config {p}: {e}")
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
    """提交用户对 clarify 的回答到 QuestionManager

    多问题支持：
    - 新通道：前端传 answers 对象 {问题文本: 答案}（多问题卡片），直接透传；
    - 旧通道：仅 answer 字符串（单问题 / skip / 旧前端），包装为 {"0": answer}。

    幂等护栏：仅接受"当前待答问题"的 request_id；无待答问题或 id 不匹配时
    直接忽略（旧卡重复提交不得污染下一次 ask 的等待事件）。
    """
    try:
        from agent_core.state.question import QuestionManager
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
        "scope": entry.get("scope", "project"),
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


# ==================== 用户便签 (usernote) ====================
_USERRNOTE_PATH = _DATA_DIR / "usernote.json"
_THEME_PATH = _DATA_DIR / "theme.json"


def _load_theme() -> dict:
    """读取主题配置；缺失或损坏时返回默认结构。"""
    try:
        if _THEME_PATH.exists():
            with open(_THEME_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "current": data.get("current") or "amber",
                    "custom": data.get("custom") if isinstance(data.get("custom"), list) else [],
                    "colors": data.get("colors") if isinstance(data.get("colors"), dict) else {},
                }
    except Exception as e:
        logger.warning(f"[Server] load theme failed: {e}")
    return {"current": "amber", "custom": [], "colors": {}}


def _save_theme(data: dict) -> bool:
    """原子写入主题配置；缺失字段沿用当前值。"""
    try:
        current = _load_theme()
        merged = {
            "current": data.get("current") or current.get("current") or "amber",
            "custom": data.get("custom") if isinstance(data.get("custom"), list) else current.get("custom", []),
            "colors": data.get("colors") if isinstance(data.get("colors"), dict) else current.get("colors", {}),
        }
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(_THEME_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _THEME_PATH)
        return True
    except Exception as e:
        logger.warning(f"[Server] save theme failed: {e}")
        return False

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


# ── 辅助：快照路径精确匹配 ──
# 快照记录的 relative_path 相对项目根，absolute_path 为绝对路径。
# 匹配只做归一化后的完整路径相等，不做子串或文件名比较，避免 config 与
# config.json、a/utils.py 与 b/utils.py 这类路径互相命中。
def _resolve_snapshot_path(p: str, project_path: str = "") -> str:
    raw = (p or "").strip()
    if not raw:
        return ""
    if not os.path.isabs(raw) and project_path:
        raw = os.path.join(project_path, raw)
    try:
        resolved = os.path.abspath(raw)
    except (OSError, ValueError):
        return ""
    return os.path.normcase(os.path.normpath(resolved)).replace("\\", "/")


def _snapshot_path_equal(p: str, target_abs: str, project_path: str = "") -> bool:
    if not p or not target_abs:
        return False
    return _resolve_snapshot_path(p, project_path) == target_abs


def _snapshot_record_matches(record, target_abs: str, project_path: str = "") -> bool:
    return (
        _snapshot_path_equal(getattr(record, "absolute_path", "") or "", target_abs, project_path)
        or _snapshot_path_equal(getattr(record, "relative_path", "") or "", target_abs, project_path)
    )


def handle_request(method: str, params: dict, rid: str) -> Optional[dict]:
    global _current_agent
    llm_config = load_llm_config(_current_agent)
    active_model = llm_config.get("model", "deepseek-chat")
    ctx = RpcContext(
        srv=sys.modules[__name__],
        rid=rid,
        params=params,
        llm_config=llm_config,
        active_model=active_model,
    )
    return dispatch(ctx, method)


_PLACEHOLDER_TITLE = "New conversation"


async def _apply_generated_title(
    sid: str,
    user_text: str,
    assistant_text: str,
    llm_config: dict,
    write_event,
) -> None:
    """Name a session from its first completed turn.

    The placeholder check runs first so later turns never spend a model call.
    The write is a compare-and-set, so a rename made while the turn was running
    always wins over the generated title. Failures stay silent: the reply and
    its persistence are already done, and the placeholder title is a valid state.
    """
    try:
        session = _session_store.get(sid) or {}
    except Exception as e:
        logger.warning(f"[Server] Title guard read failed for {sid}: {e}")
        return
    if (session.get("title") or "") != _PLACEHOLDER_TITLE:
        return

    title = await generate_session_title(llm_config, user_text, assistant_text)
    if not title or title == _PLACEHOLDER_TITLE:
        return

    try:
        applied = _session_store.update_title_if_current(sid, title, _PLACEHOLDER_TITLE)
    except Exception as e:
        logger.warning(f"[Server] Failed to persist generated title for {sid}: {e}")
        return
    if not applied:
        logger.info(f"[Server] Generated title for {sid} discarded: title changed meanwhile")
        return

    logger.info(f"[Server] Session {sid} named by LLM: {title!r}")
    write_event("session.updated", {"title": title, "session_id": sid})


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
                except (TypeError, ValueError): logger.debug("[Server] Ignoring invalid temperature: %r", temp_val)
            tp_val = prov.get("topP")
            if tp_val is not None:
                try: top_p = float(tp_val)
                except (TypeError, ValueError): logger.debug("[Server] Ignoring invalid topP: %r", tp_val)
    except Exception as e:
        logger.warning(f"[Server] Failed to load provider generation settings: {e}")

    _session_store.increment_messages(sid)
    user_msg_id, user_msg_uuid = _session_store.add_user_message(sid, text)

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
                                    # 以快照记录为准取相对路径和可回退版本数，避免工具参数
                                    # 传绝对路径时前端出现重复行或版本数错误
                                    rel_path = file_path
                                    active_count = 0
                                    try:
                                        from agent_core.snapshot.snapshot_manager import get_snapshot_manager
                                        _proj = _session_project_paths.get(sid, str(_PROJECT_ROOT))
                                        _mgr = get_snapshot_manager(sid, _proj)
                                        if _mgr:
                                            _proj = getattr(_mgr, 'project_path', _proj)
                                            _target = _resolve_snapshot_path(file_path, _proj)
                                            _recs = [r for r in _mgr.query_all_active()
                                                     if _snapshot_record_matches(r, _target, _proj)]
                                            if _recs:
                                                rel_path = _recs[0].relative_path
                                                active_count = sum(1 for r in _recs if r.revert_status == 'active')
                                    except Exception as _se:
                                        logger.warning(f"[file.change] snapshot lookup failed: {_se}")
                                    logger.info(f"[file.change] sid={sid} tool={tool_name} path={rel_path!r} versions={active_count}")
                                    write_event("file.change", {"relative_path": rel_path, "active_count": active_count, "operation_type": tool_name.replace("_file", ""), "tool_name": tool_name, "tool_id": tool_id})
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
                            "prompt": q.get("prompt", "") if isinstance(q, dict) else str(q),
                            "title": (q.get("title", "QUESTION") if isinstance(q, dict) else "QUESTION"),
                            "options": [
                                {"label": c.get("label", ""), "detail": c.get("detail", "")} if isinstance(c, dict) else {"label": str(c), "detail": ""}
                                for c in (q.get("options", []) if isinstance(q, dict) else [])
                            ],
                            "multiple": bool(q.get("multiple", False)) if isinstance(q, dict) else False,
                        } for q in qs]
                        write_event("clarify.request", {
                            "request_id": event.get("question_id", str(uuid.uuid4())),
                            "question": q_full[0]["prompt"],  # 兼容旧前端
                            "choices": [o["label"] for o in q_full[0]["options"]],    # 兼容旧前端
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
                if not was_interrupted:
                    # Runs after the reply is persisted and pushed, so the user is
                    # never waiting on it; a failure leaves the placeholder title.
                    await _apply_generated_title(sid, text, full_content, llm_config, write_event)
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
