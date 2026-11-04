import asyncio
import concurrent.futures
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
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
from agent_core.llm_client import build_default_headers, normalize_base_url

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


def load_agent_skills() -> list:
    """Skills preloaded into the current agent's system prompt.

    `load_skills_info()` answers a different question: it lists every skill
    directory on disk, so a panel fed from it describes the catalogue rather
    than this conversation. The preload list is the `skills:` field of the
    active agent's yaml, the same list agent_loader appends to the prompt.
    """
    try:
        from agent_core.agent_loader import get_cached_agent
        agent = get_cached_agent(_current_agent)
        return [str(name) for name in (agent.skills or [])] if agent else []
    except Exception as e:
        logger.warning(f"[Server] Failed to read agent skills: {e}")
        return []


def _get_llm_client(session_id: str = "") -> tuple:
    """获取当前 LLM 客户端和配置"""
    llm_config = load_llm_config(_current_agent)
    model = llm_config.get("model", "deepseek-chat")
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url", "")
    base_url = normalize_base_url(base_url)
    if not api_key or not base_url:
        # 回退到 provider 配置
        try:
            data = json.loads(open(_PROVIDERS_PATH, "r", encoding="utf-8").read())
            providers = data.get("providers", [])
            idx = data.get("activeProviderIndex", 0)
            if providers and 0 <= idx < len(providers):
                prov = providers[idx]
                api_key = prov.get("apiKey", api_key)
                base_url = normalize_base_url(prov.get("baseUrl", base_url))
                model = (prov.get("models") or [model])[0]
        except Exception as e:
            logger.warning(f"[Server] Failed to load llm_providers.json overrides: {e}")
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=build_default_headers(llm_config, session_id),
    )
    return client, model


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write a small JSON config without ever exposing a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        # Keep the previous file one generation back. It also preserves a
        # corrupt file for inspection if the user saves again after a read error.
        if path.exists():
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError as exc:
                logger.warning(f"[Server] Could not back up {path}: {exc}")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_providers() -> dict:
    # A missing user file may fall back to the bundled copy. An existing file
    # that fails to parse must not: showing bundled sample keys would hide the
    # corruption and a later save could overwrite the real file with samples.
    for p in [_PROVIDERS_PATH, _BUNDLED_PROVIDERS_PATH]:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Server] Failed to read llm_providers.json from {p}: {e}")
            return {"providers": [], "activeProviderIndex": 0}
    return {"providers": [], "activeProviderIndex": 0}


def _write_providers(data: dict):
    _atomic_write_json(_PROVIDERS_PATH, data)


def _norm_project_path(path: str) -> str:
    """Normalise a project path without inventing one.

    Empty input stays empty: ``os.path.abspath("")`` is the process cwd, and in
    a packaged app that is the sidecar's install directory. Treating it as a
    project would register, or adopt sessions into, a folder nobody picked.
    """
    if not isinstance(path, str):
        return ""
    path = path.strip()
    if not path:
        return ""
    return os.path.normpath(os.path.abspath(path))


def _validate_project_dir(path: str) -> tuple:
    """
    校验项目目录：
    必须存在、是目录、且非盘根（盘根会使所有路径边界检查失效）。
    Returns: (ok, error, resolved_path)
    """
    if not path:
        return False, "path required", ""
    expanded = os.path.expandvars(os.path.expanduser(path))
    resolved = os.path.realpath(expanded)
    if not os.path.isdir(resolved):
        return False, f"not a directory: {path}", ""
    _drive, tail = os.path.splitdrive(resolved)
    if not tail or tail in ("\\", "/"):
        return False, "drive root is not a valid project path", ""
    # The checks above run against the realpath, which is what makes a symlink or
    # junction safe to validate. What gets stored is the path as the user wrote
    # it: on Windows realpath also resolves the 8.3 short name
    # (`C:\Users\RUNNER~1\...`), and a registry entry in that form disagreed with
    # every path the rest of the app derives with abspath.
    return True, "", _norm_project_path(expanded)


def _preserve_corrupt_projects(reason: str) -> str:
    """Copy an unreadable projects.json aside so a later save cannot erase it.

    The original file stays where it is: it is the user's only copy of the
    registry, and a read failure must never be turned into "no projects". The
    copy is written once, so repeated failures do not pile up files.
    """
    try:
        if not _PROJECTS_PATH.exists():
            return ""
        backup = _PROJECTS_PATH.with_name(_PROJECTS_PATH.name + ".corrupt")
        if not backup.exists():
            shutil.copy2(_PROJECTS_PATH, backup)
            logger.error(f"[Server] projects.json unreadable ({reason}); preserved at {backup}")
        else:
            # The copy is already there. Repeating the error on every read (a
            # prompt submit reads the registry) only floods the log.
            logger.warning(f"[Server] projects.json still unreadable ({reason}); kept the existing copy at {backup}")
        return str(backup)
    except Exception as e:
        logger.error(f"[Server] Could not preserve the unreadable projects.json: {e}")
        return ""


def _read_projects() -> dict:
    """读取项目列表 {active, projects[]}。

    未选择项目时返回空列表，交给前端引导用户指定。用服务当前工作目录做
    默认值是不可靠的：打包后它是 sidecar 的工作目录（取决于启动方式，
    可能是安装目录），会诱使用户把文件写进安装位置。

    读不出来和"没有项目"是两种状态：损坏的文件先复制一份再返回空表，并把
    ``corrupt`` 带出去，避免上层把一次读失败当成空注册表继续写。
    """
    if not _PROJECTS_PATH.exists():
        return {"active": "", "projects": []}
    try:
        with open(_PROJECTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
            raise ValueError("projects.json has no projects list")
        projects = []
        for raw in data["projects"]:
            norm = _norm_project_path(raw) if raw else ""
            if norm and norm not in projects:
                projects.append(norm)
        # `active` is normalised as well: an older file may carry a trailing
        # separator or a different case, and an exact string compare used to
        # silently reselect projects[0] behind the user's back.
        active = _norm_project_path(data.get("active") or "")
        if active not in projects:
            active = projects[0] if projects else ""
        return {"active": active, "projects": projects}
    except Exception as e:
        backup = _preserve_corrupt_projects(str(e))
        return {"active": "", "projects": [], "corrupt": True, "backup": backup}


def _write_projects(data: dict):
    """Persist the registry atomically, never over an unreadable original.

    projects.json decides which folders the tools may touch. A truncated file
    reads as "no projects" and the next save makes that permanent, so this goes
    through the same atomic writer (temp file + fsync + replace + .bak) as the
    provider and agent configs.
    """
    if _PROJECTS_PATH.exists():
        try:
            with open(_PROJECTS_PATH, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception:
            _preserve_corrupt_projects("unreadable before write")
    # Only the two keys the registry actually owns are persisted. `_read_projects`
    # also answers `corrupt` / `backup`, and writing them back would leave a
    # transient read error recorded in the file it describes.
    clean = {
        "active": str(data.get("active") or ""),
        "projects": [str(p) for p in (data.get("projects") or []) if p],
    }
    _atomic_write_json(_PROJECTS_PATH, clean)


def _read_agent_model_config() -> dict:
    for p in [_AGENT_MODEL_PATH, _BUNDLED_AGENT_MODEL_PATH]:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Server] Failed to read agent model config {p}: {e}")
            return {"version": "1.0", "agents": {}}
    return {"version": "1.0", "agents": {}}


def _write_agent_model_config(data: dict):
    _atomic_write_json(_AGENT_MODEL_PATH, data)


def restore_current_agent() -> None:
    """Restore the agent selected before the last restart."""
    global _current_agent
    try:
        cfg = _read_agent_model_config()
        name = str(cfg.get("current_agent") or "").strip()
        if not name:
            return
        available = {
            str(a.get("name") or "").strip()
            for a in list_available_agents_from_yaml()
            if str(a.get("name") or "").strip()
        }
        if name in available:
            _current_agent = name
            logger.info(f"[Server] Restored agent selection: {name}")
    except Exception as e:
        logger.warning(f"[Server] Failed to restore agent selection: {e}")


def _ensure_service() -> AgentChatServiceV2:
    global _current_service
    if _current_service is None:
        _current_service = AgentChatServiceV2()
        # 进程的 cwd 是 sidecar 的启动目录（安装目录下的可执行文件所在处），不是
        # 用户选的项目，所以服务初始化后立刻把注册表里的 active 装回去。否则在
        # 用户主动切换项目之前，提示词里的工作目录一直是那个安装目录。
        try:
            active = (_read_projects() or {}).get("active") or ""
        except Exception:
            active = ""
        if active and os.path.isdir(active):
            try:
                _current_service.set_project_path(active)
                logger.info(f"[Server] Restored active project: {active}")
            except Exception as e:
                logger.warning(f"[Server] Failed to restore active project {active}: {e}")
        elif active:
            logger.warning(f"[Server] Active project missing or not a directory: {active}")
    return _current_service


# ---- cross-client event fan-out -------------------------------------------
# The stdio entry point has no socket list, so the WebSocket server registers a
# broadcaster here at startup. RPC handlers that change state every client cares
# about (a clarification answered, a permission decided) call `broadcast_event`
# so the phone and the desktop both hear about it.
_event_broadcaster = None


def set_event_broadcaster(fn):
    global _event_broadcaster
    _event_broadcaster = fn


def broadcast_event(event_type: str, payload: dict):
    fn = _event_broadcaster
    if fn is None:
        return
    try:
        fn(event_type, payload)
    except Exception as e:
        logger.warning(f"[Gateway] broadcast_event({event_type}) failed: {e}")


# The prompt worker registry lives in the WebSocket server, which is not loaded
# in every entry point (the stdio one has no sockets). RPC handlers ask through
# this hook so `session.resume` can report whether a turn is still running
# instead of guessing from client-side event traffic.
_session_running_probe = None
# Same registry, asked the broader question: is *any* worker alive. A project
# switch is global, so it has to see turns started by other clients.
_any_session_running_probe = None


def set_session_running_probe(fn):
    global _session_running_probe
    _session_running_probe = fn


def is_session_running(session_id: str) -> bool:
    fn = _session_running_probe
    if fn is None or not session_id:
        return False
    try:
        return bool(fn(session_id))
    except Exception:
        return False


def set_any_session_running_probe(fn):
    global _any_session_running_probe
    _any_session_running_probe = fn


def is_any_session_running() -> bool:
    """True while any prompt worker is still alive.

    Switching the project root is a global operation: a running turn resolves
    every later tool call against the service's current path, so the guard
    cannot look only at the session named in the request. A client that sends no
    session id must not be able to switch underneath somebody else's turn.
    """
    fn = _any_session_running_probe
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception:
        return False


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
        # Read the owner before submitting: the answer clears the pending state.
        owner_sid = QuestionManager.get_pending_session_id()
        if answers:
            # 新通道：完整答案对象（键=问题文本，自描述，agent 可精确对应）
            QuestionManager.submit_answers({"answers": answers})
        else:
            # 旧通道：单答案字符串（兼容单问题 / skip）
            QuestionManager.submit_answers({"answers": {"0": answer}})
        # Every client must drop its clarify card, not just the one that answered.
        broadcast_event("clarify.resolved", {
            "request_id": pending_id,
            "session_id": owner_sid,
            "answer": answer,
        })
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


# ==================== 个人笔记本 (notebook) ====================
# 用户自己的记事本：只给本人看，不进入任何提示词，不随项目路径变化。
# 与 usernote / quick_notes 同放在 _DATA_DIR（~/.pebble/），但写入先落到临时
# 文件再 os.replace，掉电或崩溃不会留下半截 JSON。
_NOTEBOOK_PATH = _DATA_DIR / "notebook.json"


def _load_notebook() -> list:
    """读取全部条目：非字典条目丢弃，空文本不入表，缺字段补空串。"""
    try:
        if _NOTEBOOK_PATH.exists():
            with open(_NOTEBOOK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries", []) if isinstance(data, dict) else data
            if isinstance(entries, list):
                out = []
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    out.append({
                        "id": str(item.get("id") or "") or os.urandom(4).hex(),
                        "time": str(item.get("time") or ""),
                        "text": text,
                    })
                return out
    except Exception as e:
        logger.warning(f"[Server] load notebook failed: {e}")
    return []


def _save_notebook(entries: list) -> None:
    """整表覆盖写入；空文本条目不入库，与前端“空条目即丢弃”一致。"""
    clean = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        clean.append({
            "id": str(item.get("id") or "") or os.urandom(4).hex(),
            "time": str(item.get("time") or ""),
            "text": text,
        })
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = str(_NOTEBOOK_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"entries": clean, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _NOTEBOOK_PATH)


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

    title = await generate_session_title(llm_config, user_text, assistant_text, sid)
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


def _persist_interrupted_reply(
    sid: str,
    event: Optional[dict],
    full_content: str,
    full_reasoning: str,
    tool_executions: list,
    err_msg: str,
) -> tuple[bool, Optional[int]]:
    """把中断回合已生成的内容连同错误行一起落库，返回 (是否写入, 消息 id)。

    传输层异常（httpx "peer closed connection ... incomplete chunked read"）不会
    走到 chat_completed，不在异常路径补写，用户当下看到的正文会在刷新或重启后
    消失。正文格式与前端的 error 分支保持一致（正文 + 空行 + **Error:** ...），
    刷新前后是同一条消息、同样的文本；msg_uuid 必须沿用 agent 侧那条记录的 uuid，
    否则 message.delete 按 uuid 同步删除 agent_chat.db 会失败，变成假删除。
    """
    payload = event or {}
    partial = payload.get("content") or full_content or ""
    reasoning = payload.get("reasoning") or full_reasoning or ""
    execution_order = payload.get("execution_order") or tool_executions or []
    msg_uuid = payload.get("assistant_msg_uuid") or ""
    error_line = f"**Error:** {err_msg}"
    content = f"{partial}\n\n{error_line}" if partial.strip() else error_line
    try:
        message_id, _ = _session_store.add_assistant_message(
            sid,
            content,
            reasoning or None,
            json.dumps(execution_order) if execution_order else None,
            msg_uuid=msg_uuid,
            interrupted=True,
        )
        return True, message_id
    except Exception as _se:
        logger.warning(f"[Server] Failed to save interrupted reply for {sid}: {_se}")
        return False, None


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

    llm_config = load_llm_config(_current_agent)
    service = _ensure_service()
    agent_name = _current_agent

    # 本轮的工作目录：前端传了就用它，没传就落到注册表的 active，再退到服务当前
    # 路径。cwd 不能作为兜底——打包后那是 sidecar 的启动目录，跟会话无关。
    if not project_path:
        try:
            project_path = (_read_projects() or {}).get("active") or ""
        except Exception:
            project_path = ""
    if not project_path:
        project_path = getattr(service, "_current_project_path", "") or ""
    # 记录项目路径，供快照查询与工具边界使用
    if project_path:
        _session_project_paths[sid] = os.path.abspath(project_path)

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
        # 累积量必须在 try 之前初始化：流建立之前就可能抛错，异常分支仍要能
        # 安全取到已生成内容，并据此补写一条 interrupted 消息。
        full_content = ""
        full_reasoning = ""
        tool_executions = []
        was_interrupted = False
        chat_completed_data = None
        assistant_saved = False
        try:
            write_event("user_message.saved", {
                "message_id": user_msg_id,
                "session_id": sid,
                # The text travels with the id so the other client can render a
                # message it never sent; an id alone only lets the sender stamp
                # a row that already exists on its side.
                "text": text,
                "created_at": time.time(),
            })
            gen = service.chat_stream(
                agent_name=agent_name, message=text, conversation_uuid=sid,
                model=llm_config.get("model", "gpt-4"), project_path=project_path,
                temperature=temperature, top_p=top_p,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
                attachments=attachments, user_msg_uuid=user_msg_uuid)
            async for event in gen:
                if cancel_event.is_set():
                    logger.info(f"[Server] Chat cancelled by user for session {sid}")
                    was_interrupted = True
                event_type = event.get("type", "")
                if event_type == "content":
                    chunk = event.get("content", "")
                    full_content += chunk
                    write_event("message.delta", {"text": chunk, "tokens": event.get("tokens", 0)})
                elif event_type == "reasoning":
                    chunk = event.get("content", "")
                    full_reasoning += chunk
                    write_event("thinking.delta", {"text": chunk, "tokens": event.get("tokens", 0)})
                elif event_type == "tool_call_streaming":
                    tool_id = event.get("tool_call_id", "")
                    tool_name = event.get("tool_name", "")
                    args_full = event.get("arguments_full", "")
                    if tool_id:
                        write_event("tool.call_streaming", {"tool_id": tool_id, "name": tool_name, "args_text": args_full, "tokens": event.get("tokens", 0)})
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
                            _session_store.add_tool_message(sid, tool_name, ctx, tool_call_id=tcid, output=tool_output)
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
                    # An empty list is a real state: the agent cleared its plan and
                    # the card has to close. Dropping it left the previous list on
                    # screen, because the frontend only learns about a clear here.
                    todos = event.get("todos", [])
                    write_event("todos.update", {"todos": todos if isinstance(todos, list) else []})
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
                    write_event("message.usage", {"model": event.get("model", ""), "session_cost_cny": event.get("session_cost_cny", 0), "latest_input_tokens": event.get("latest_input_tokens", 0), "latest_output_tokens": event.get("latest_output_tokens", 0), "model_context_limit": event.get("model_context_limit", 0), "period": event.get("period"), "context_breakdown": event.get("context_breakdown"), "this_call": event.get("this_call")})
                elif event_type == "chat_completed":
                    chat_completed_data = event
                    break
                elif event_type == "error":
                    err_msg = event.get("message", "Unknown error")
                    # 已生成的正文必须随错误一起落库：只写错误行会让用户当下看到的
                    # 那段输出在刷新或重启后消失，只剩一行 Error。
                    assistant_saved, message_id = _persist_interrupted_reply(
                        sid, event, full_content, full_reasoning, tool_executions, err_msg
                    )
                    write_event("error", {
                        "message": err_msg,
                        "interrupted": True,
                        "message_id": message_id,
                        "session_id": sid,
                        "content": event.get("content") or full_content,
                        "reasoning": event.get("reasoning") or full_reasoning,
                        "execution_order": event.get("execution_order") or tool_executions,
                    })
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
                    assistant_saved = True
                except Exception as _se:
                    logger.warning(f"[Server] Failed to save assistant message for {sid}: {_se}")
                write_event("message.complete", {"text": full_content, "reasoning": full_reasoning, "tool_calls": json.dumps(tool_executions), "interrupted": was_interrupted, "message_id": chat_completed_data.get("message_id"), "session_id": sid})
                if not was_interrupted:
                    # Runs after the reply is persisted and pushed, so the user is
                    # never waiting on it; a failure leaves the placeholder title.
                    await _apply_generated_title(sid, text, full_content, llm_config, write_event)
            elif not assistant_saved:
                # 走到这里说明生成器结束却没发 chat_completed：正常的兜底保存。
                # error 分支已经写过一条带正文的中断消息时跳过，否则同一条回复会
                # 在库里出现两行，且随后的 message.complete 会把前端的错误标记抹掉。
                try:
                    _session_store.add_assistant_message(sid, full_content, full_reasoning, json.dumps(tool_executions) if tool_executions else None, interrupted=was_interrupted)
                    assistant_saved = True
                except Exception as _se:
                    logger.warning(f"[Server] Failed to save assistant message for {sid}: {_se}")
                write_event("message.complete", {"text": full_content, "reasoning": full_reasoning, "tool_calls": json.dumps(tool_executions), "interrupted": was_interrupted, "session_id": sid})
        except Exception as e:
            logger.error(f"[Server] Chat error: {e}")
            # 传输层异常（上游断流、读取超时）走不到 chat_completed，这里必须把
            # 已生成的正文补写进库，否则刷新或重启后这段输出消失，只剩一行 Error。
            err_msg = str(e)
            message_id = None
            if not assistant_saved:
                assistant_saved, message_id = _persist_interrupted_reply(
                    sid, None, full_content, full_reasoning, tool_executions, err_msg
                )
            write_event("error", {
                "message": err_msg,
                "interrupted": True,
                "message_id": message_id,
                "session_id": sid,
                "content": full_content,
                "reasoning": full_reasoning,
                "execution_order": tool_executions,
            })

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
