"""MCP management RPCs."""

import asyncio
import concurrent.futures
import json
import logging
import os
import shutil

logger = logging.getLogger(__name__)


def _is_unusable_config(raw) -> bool:
    """配置本身无法连接：命令为空或仍是占位符，或远程地址为空。"""
    command = getattr(raw, "command", None)
    if command is not None:
        text = str(command).strip()
        return (not text) or ("REPLACE_WITH" in text)
    url = getattr(raw, "url", None)
    if url is not None:
        return not str(url).strip()
    return False


def _installable_server_names(srv) -> set:
    """mcp_install.json 声明的可安装服务器（这些即使配置占位也保留在面板里）。"""
    return set(_install_info(srv).get("servers", {}) or {})


def _install_info(srv) -> dict:
    """mcp_install.json 的完整内容；没有这个文件时返回空字典。"""
    for p in (srv._DATA_DIR / "mcp_install.json", srv._PROJECT_ROOT / "mcp_install.json"):
        try:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def _command_readiness(command, args=None) -> tuple:
    """这个 command 能不能启动，以及缺的是什么。

    返回 ``(ready, missing)``。npx / uvx 是运行时外壳，缺的是它们背后的 node / uv，
    所以报告缺少的是依赖而不是外壳本身；占位符路径报告 ``path``，由面板提示用户
    下载或手动指定。
    """
    cmd = str(command or "").strip()
    if not cmd or "REPLACE_WITH" in cmd:
        return False, ["path"]
    base = os.path.basename(cmd).lower()
    if base in ("npx", "npx.cmd"):
        return (True, []) if shutil.which("npx") else (False, ["node"])
    if base in ("uvx", "uvx.exe"):
        return (True, []) if shutil.which("uvx") else (False, ["uv"])
    if os.path.isabs(cmd):
        return (True, []) if os.path.isfile(cmd) else (False, ["path"])
    found = shutil.which(cmd)
    return (bool(found), [] if found else [base or cmd])


def _token_env(env) -> str:
    """env 里承载凭据的变量名，没有则返回空字符串。

    面板据此决定要不要画 token 输入框，并且知道该写回哪个变量：把名字固定
    成 MCP_TOKEN 会让 github 那种只认 GITHUB_PERSONAL_ACCESS_TOKEN 的服务器
    收到一个它从不读取的键。
    """
    if not isinstance(env, dict):
        return ""
    for key in env:
        k = str(key).upper()
        if k.endswith("TOKEN") or k.endswith("API_KEY") or k.endswith("_KEY"):
            return str(key)
    return ""


def _requires_token(env) -> bool:
    return bool(_token_env(env))


def _prereq_ready(name: str) -> bool:
    """mcp_install.json 的 requires 条目是否已满足。"""
    key = str(name or "").lower()
    if key == "node":
        return shutil.which("npx") is not None
    if key == "uv":
        return shutil.which("uvx") is not None
    # chrome 之类无法静态探测，交给运行时报告
    return True


def _read_json_object(path) -> dict:
    """读一个 JSON 对象；文件缺失或损坏时返回空字典。"""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"[Server] cannot read {path}: {e}")
    return {}


def _config_fingerprint(scoped) -> str:
    """配置指纹，用于判断服务器配置是否变化需要重连。"""
    raw = getattr(scoped, "config", scoped)
    parts = [
        str(getattr(raw, "command", "") or ""),
        " ".join(str(a) for a in (getattr(raw, "args", None) or [])),
        str(getattr(raw, "url", "") or ""),
        json.dumps(getattr(raw, "env", None) or {}, sort_keys=True, default=str),
    ]
    return "|".join(parts)


def _drop_servers(srv, mgr, names) -> None:
    """断开并从内存里移除这些服务器。

    Submitted onto the WS loop instead of awaited: `mcp.list` must answer with
    the reconciled view immediately, and closing a transport is the slow half.
    """
    if not names or not getattr(mgr, "_initialized", False):
        return
    loop = getattr(srv, "_ws_loop", None)
    if not loop:
        return

    async def _drop():
        for name in names:
            cur = mgr._clients.get(name)
            if cur and getattr(cur, "_client", None):
                try:
                    await cur._client.close()
                except Exception:
                    pass
            mgr._clients.pop(name, None)
            mgr._configs.pop(name, None)
            mgr._update_tools_for_server(name, [])
        mgr._notify_state_change()

    try:
        asyncio.run_coroutine_threadsafe(_drop(), loop)
    except Exception as e:
        logger.warning(f"[Server] failed to drop MCP servers {names}: {e}")


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "mcp.list":
        servers = []
        tools_list = []
        pending = []
        pending_user = []
        available = []
        try:
            from agent_core.mcp.mcp_config import (
                get_mcp_config_manager, get_pending_project_mcp, pending_user_overrides,
                sync_user_overrides,
            )
            from agent_core.mcp.mcp_manager import get_mcp_manager

            cfg_mgr = get_mcp_config_manager()
            cfg_mgr.set_project_path(str(srv._PROJECT_ROOT))

            # The file is the truth. Nothing re-reads it between launches, so a
            # server deleted from it would otherwise stay on the rack — and stay
            # connected — until the backend restarted.
            _, vanished = sync_user_overrides()
            if vanished:
                _drop_servers(srv, get_mcp_manager(), vanished)

            all_configs = cfg_mgr.get_all_configs()

            mgr = get_mcp_manager()
            state = mgr.get_state() if mgr._initialized else None
            clients = {c.name: c for c in state.clients} if state else {}
            server_tools: dict = {}
            if state:
                for t in state.tools:
                    sn = t.server_name or "unknown"
                    server_tools.setdefault(sn, []).append(t.name)

            installable = _installable_server_names(srv)
            for name, scoped in all_configs.items():
                raw = getattr(scoped, "config", scoped)
                # 隐藏无法连接也无法安装的失效配置（如占位符命令）
                if _is_unusable_config(raw) and name not in installable:
                    continue
                conn = clients.get(name)
                conn_type = getattr(conn, "type", "") if conn else ""
                enabled = not cfg_mgr.is_server_disabled(name)
                tnames = server_tools.get(name, [])
                if not enabled:
                    conn_type = "disabled"
                elif not conn_type:
                    conn_type = "not-started"
                ready, missing = _command_readiness(
                    getattr(raw, "command", None), getattr(raw, "args", None)
                )
                servers.append({
                    "name": name,
                    "transport": getattr(raw, "type", None) or "stdio",
                    "command": getattr(raw, "command", None) or getattr(raw, "url", "") or "",
                    "connected": conn_type == "connected",
                    "enabled": enabled,
                    "state": conn_type,
                    "tools": len(tnames),
                    "tool_names": tnames,
                    "error": getattr(conn, "error", None) if conn else None,
                    "ready": ready,
                    "missing": missing,
                    "requires_token": _requires_token(getattr(raw, "env", None)),
                    "token_env": _token_env(getattr(raw, "env", None)),
                    "installable": name in installable,
                })
            tools_list = [
                {"server": s["name"], "name": tn}
                for s in servers for tn in s["tool_names"]
            ]
            pending = get_pending_project_mcp(str(srv._PROJECT_ROOT))
            pending_user = pending_user_overrides()

            # mcp_install.json 里声明、但 .mcp.json 还没配的服务器：面板据此画安装入口。
            for aname, entry in (_install_info(srv).get("servers") or {}).items():
                if aname in all_configs or not isinstance(entry, dict):
                    continue
                requires = [str(r) for r in (entry.get("requires") or [])]
                available.append({
                    "name": aname,
                    "desc": entry.get("description") or "",
                    "type": entry.get("type") or "",
                    "requires": requires,
                    "missing": [r for r in requires if not _prereq_ready(r)],
                })
        except Exception as e:
            logger.warning(f"[Server] mcp.list failed: {e}")
        return {"id": rid, "result": {"servers": servers, "tools": tools_list, "available": available, "pending_project_servers": pending, "pending_user_servers": pending_user}}

    if method == "mcp.toggle":
        """连接/断开指定 MCP 服务器（持久化到全局禁用列表，热生效）。
        参数：server（必填）、enabled（可选，缺省取反当前状态）。"""
        server_name = str(params.get("server") or "").strip()
        if not server_name:
            return {"id": rid, "error": {"code": -32602, "message": "server required"}}
        try:
            from agent_core.mcp.mcp_config import get_mcp_config_manager
            from agent_core.mcp.mcp_manager import get_mcp_manager

            cfg_mgr = get_mcp_config_manager()
            cfg_mgr.set_project_path(str(srv._PROJECT_ROOT))
            if server_name not in cfg_mgr.get_all_configs():
                return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' is not configured"}}

            desired = params.get("enabled")
            desired = (not cfg_mgr.is_server_disabled(server_name)) if desired is None else bool(desired)

            mgr = get_mcp_manager()
            if not mgr._initialized:
                # 管理器尚未启动：只落配置，首次请求按新配置初始化
                cfg_mgr.set_server_enabled(server_name, desired)
                return {"id": rid, "result": {"ok": True, "enabled": desired, "state": "not-started"}}

            if srv._ws_loop:
                fut = asyncio.run_coroutine_threadsafe(
                    mgr.set_server_enabled_state(server_name, desired), srv._ws_loop
                )

                def _done(f, _sn=server_name, _en=desired):
                    try:
                        f.result()
                        logger.info(f"[Server] MCP '{_sn}' enabled={_en} applied")
                    except (asyncio.CancelledError, concurrent.futures.CancelledError):
                        # 取消不代表配置未生效，以实际状态为准
                        try:
                            actual_enabled = not cfg_mgr.is_server_disabled(_sn)
                            actual_client = get_mcp_manager()._clients.get(_sn)
                            logger.info(
                                f"[Server] MCP '{_sn}' toggle cancelled; "
                                f"actual enabled={actual_enabled} "
                                f"state={getattr(actual_client, 'type', 'unknown')}"
                            )
                        except Exception:
                            logger.info(f"[Server] MCP '{_sn}' toggle cancelled")
                    except Exception as e:
                        logger.warning(f"[Server] MCP '{_sn}' toggle failed: {type(e).__name__}: {e}")

                fut.add_done_callback(_done)
            else:
                cfg_mgr.set_server_enabled(server_name, desired)

            return {"id": rid, "result": {
                "ok": True,
                "enabled": desired,
                "state": "connecting" if desired else "disabled",
            }}
        except Exception as e:
            logger.error(f"[Server] mcp.toggle failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "mcp.reload":
        """重新加载 MCP 配置：重建 dynamic 层；新增的连接，消失的断开，变更的重连。"""
        logger.info("[Server] MCP reload requested")
        try:
            from agent_core.mcp.mcp_config import (
                build_user_override_configs, get_mcp_config_manager,
            )
            from agent_core.mcp.mcp_manager import get_mcp_manager

            cfg_mgr = get_mcp_config_manager()
            cfg_mgr.set_project_path(str(srv._PROJECT_ROOT))
            overrides = build_user_override_configs()
            if overrides:
                cfg_mgr.set_dynamic_configs(overrides)

            enabled = cfg_mgr.get_enabled_configs()
            mgr = get_mcp_manager()
            if not mgr._initialized:
                return {"id": rid, "result": {"ok": True, "applied": "on-next-init"}}

            added = [n for n in enabled if n not in mgr._configs]
            removed = [n for n in list(mgr._configs) if n not in enabled]
            changed = [
                n for n in enabled
                if n in mgr._configs
                and _config_fingerprint(enabled[n]) != _config_fingerprint(mgr._configs[n])
            ]

            async def _apply():
                for name in removed:
                    try:
                        cur = mgr._clients.get(name)
                        if cur and getattr(cur, "_client", None):
                            await cur._client.close()
                        mgr._clients.pop(name, None)
                        mgr._configs.pop(name, None)
                        mgr._update_tools_for_server(name, [])
                    except Exception as e:
                        logger.warning(f"[Server] MCP reload remove '{name}' failed: {e}")
                for name in added + changed:
                    try:
                        mgr._configs[name] = enabled[name]
                        await mgr.reconnect_server(name)
                    except Exception as e:
                        logger.warning(f"[Server] MCP reload connect '{name}' failed: {e}")
                mgr._notify_state_change()

            if srv._ws_loop:
                asyncio.run_coroutine_threadsafe(_apply(), srv._ws_loop)
                applied = "async"
            else:
                applied = "skipped"

            return {"id": rid, "result": {
                "ok": True, "applied": applied,
                "added": added, "removed": removed, "changed": changed,
            }}
        except Exception as e:
            logger.error(f"[Server] mcp.reload failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

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
                cfg_mgr.set_project_path(str(srv._PROJECT_ROOT))
                mgr = get_mcp_manager()
                if mgr._initialized:
                    new_configs = cfg_mgr.get_all_configs()
                    for name, cfg in new_configs.items():
                        if name not in mgr._configs:
                            mgr._configs[name] = cfg
                            if srv._ws_loop:
                                _asyncio.run_coroutine_threadsafe(mgr.reconnect_server(name), srv._ws_loop)
                                reconnected.append(name)
            except Exception as e:
                logger.warning(f"[Server] mcp.trust_project hot reload failed: {e}")
            logger.info(f"[Server] mcp.trust_project approved: {path} (reconnected={reconnected})")
            return {"id": rid, "result": {"ok": True, "reconnected": reconnected}}
        except Exception as e:
            logger.error(f"[Server] mcp.trust_project failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "mcp.approve_server":
        """批准用户级 .mcp.json 里的一条配置，并让它立即连接。

        批准绑定的是条目当前内容：之后再改 command / args / env，指纹随之变化，
        条目会重新回到待批准状态。"""
        server_name = str(params.get("server") or "").strip()
        if not server_name:
            return {"id": rid, "error": {"code": -32602, "message": "server is required"}}
        try:
            from agent_core.mcp.mcp_config import (
                approve_user_override, build_user_override_configs, get_mcp_config_manager,
            )
            from agent_core.mcp.mcp_manager import get_mcp_manager

            if not approve_user_override(server_name):
                return {"id": rid, "result": {"ok": False, "error": "entry not found in .mcp.json"}}

            cfg_mgr = get_mcp_config_manager()
            cfg_mgr.set_project_path(str(srv._PROJECT_ROOT))
            overrides = build_user_override_configs()
            cfg_mgr.set_dynamic_configs(overrides)

            mgr = get_mcp_manager()
            connecting = False
            if mgr._initialized and server_name in overrides:
                mgr._configs[server_name] = overrides[server_name]
                if srv._ws_loop:
                    asyncio.run_coroutine_threadsafe(
                        mgr.reconnect_server(server_name), srv._ws_loop
                    )
                    connecting = True
            logger.info(f"[Server] MCP user override approved: {server_name} (connecting={connecting})")
            return {"id": rid, "result": {"ok": True, "connecting": connecting}}
        except Exception as e:
            logger.error(f"[Server] mcp.approve_server failed: {e}")
            return {"id": rid, "result": {"ok": False, "error": str(e)}}

    if method == "mcp.reject_server":
        """撤销批准。条目仍留在文件里，只是不再加载。"""
        server_name = str(params.get("server") or "").strip()
        if not server_name:
            return {"id": rid, "error": {"code": -32602, "message": "server is required"}}
        try:
            from agent_core.mcp.mcp_config import (
                build_user_override_configs, get_mcp_config_manager, reject_user_override,
            )
            from agent_core.mcp.mcp_manager import get_mcp_manager

            reject_user_override(server_name)
            cfg_mgr = get_mcp_config_manager()
            cfg_mgr.set_project_path(str(srv._PROJECT_ROOT))
            overrides = build_user_override_configs()
            cfg_mgr.set_dynamic_configs(overrides)

            mgr = get_mcp_manager()
            if mgr._initialized and server_name in mgr._configs and server_name not in overrides:
                mgr._configs.pop(server_name, None)
                client = mgr._clients.pop(server_name, None)
                inner = getattr(client, "_client", None)
                if inner is not None and srv._ws_loop:
                    asyncio.run_coroutine_threadsafe(inner.close(), srv._ws_loop)
            logger.info(f"[Server] MCP user override rejected: {server_name}")
            return {"id": rid, "result": {"ok": True}}
        except Exception as e:
            logger.error(f"[Server] mcp.reject_server failed: {e}")
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
        user_mcp_path = srv._DATA_DIR / ".mcp.json"
        bundled_mcp = srv._PROJECT_ROOT / ".mcp.json"
        # Base the write on the user file alone. Copying the bundled file
        # wholesale (the old behaviour) moved every bundled server into the user
        # file the first time a token was saved; only the edited server is
        # approved, so the rest landed in the pending queue as a row of "wants
        # to run" banners that read like a security alert.
        data = _read_json_object(user_mcp_path)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            data["mcpServers"] = servers
        server_cfg = servers.get(server_name)
        if not isinstance(server_cfg, dict):
            # Not in the user file yet: copy just this one entry from the bundle.
            bundled = _read_json_object(bundled_mcp)
            candidate = (bundled.get("mcpServers") or {}).get(server_name)
            server_cfg = json.loads(json.dumps(candidate)) if isinstance(candidate, dict) else None
        if not isinstance(server_cfg, dict):
            return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' not found"}}
        env = server_cfg.get("env")
        if not isinstance(env, dict):
            env = {}
        env[env_key] = value
        server_cfg["env"] = env
        servers[server_name] = server_cfg
        srv._atomic_write_json(user_mcp_path, data)
        # The write came through an RPC, which makes it the user's own edit:
        # approve it here, or the token they just typed would land in the
        # pending queue and the server would silently stop connecting.
        from agent_core.mcp.mcp_config import approve_user_override as _approve_override
        _approve_override(server_name)
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
                if srv._ws_loop:
                    _fut = asyncio.run_coroutine_threadsafe(mgr.reconnect_server(server_name), srv._ws_loop)

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
        user_mcp_path = srv._DATA_DIR / ".mcp.json"
        bundled_mcp = srv._PROJECT_ROOT / ".mcp.json"
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
        for p in [srv._DATA_DIR / "mcp_install.json", srv._PROJECT_ROOT / "mcp_install.json"]:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        install_info = json.load(f)
                    break
                except Exception:
                    continue
        entry = (install_info.get("servers") or {}).get(server_name)
        if not isinstance(entry, dict):
            return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' is not declared in mcp_install.json"}}

        entry_type = str(entry.get("type") or "").strip()
        if entry_type == "download":
            # 2. 获取 exe 路径（手动或自动）
            if manual_path:
                exe_path = manual_path
                if not os.path.isfile(exe_path):
                    return {"id": rid, "error": {"code": -32602, "message": f"file not found: {exe_path}"}}
            else:
                try:
                    exe_path = srv._install_download_server(server_name, entry)
                except Exception as e:
                    logger.error(f"[Server] MCP install failed for '{server_name}': {e}")
                    return {"id": rid, "result": {"ok": False, "error": str(e)}}
            config = {"command": exe_path, "args": []}
        elif entry_type in ("npx", "uvx"):
            # 运行时包管理器不需要下载：把命令写进配置，首次启动由 npx/uvx 拉取。
            package = str(entry.get("package") or "").strip()
            if not package:
                return {"id": rid, "result": {"ok": False, "error": f"'{server_name}' declares no package"}}
            command = "npx" if entry_type == "npx" else "uvx"
            args = ["-y", package] if entry_type == "npx" else [package]
            ready, missing = _command_readiness(command, args)
            if not ready:
                hints = {
                    "node": "Install Node.js 18+ (https://nodejs.org/) and reopen the app",
                    "uv": "Install uv (https://docs.astral.sh/uv/getting-started/installation/) and reopen the app",
                }
                hint = hints.get((missing or [""])[0], "install the missing prerequisite")
                return {"id": rid, "result": {"ok": False,
                                              "error": f"missing prerequisite: {', '.join(missing)}. {hint}"}}
            config = {"command": command, "args": args}
        else:
            return {"id": rid, "result": {"ok": False,
                                          "error": f"'{server_name}' has an unsupported install type: {entry_type or 'none'}"}}
        # 3. 合并写入 ~/.pebble/.mcp.json（保留其他服务器）
        try:
            user_mcp_path = srv._DATA_DIR / ".mcp.json"
            # Only this entry is written; see the note in mcp.set_secret.
            data = _read_json_object(user_mcp_path)
            if not isinstance(data.get("mcpServers"), dict):
                data["mcpServers"] = {}
            data["mcpServers"][server_name] = config
            srv._atomic_write_json(user_mcp_path, data)
            # Installed through the UI, so it is approved by definition.
            from agent_core.mcp.mcp_config import approve_user_override as _approve_override
            _approve_override(server_name)
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
                if srv._ws_loop:
                    _fut = asyncio.run_coroutine_threadsafe(mgr.reconnect_server(server_name), srv._ws_loop)

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
        install_path = srv._DATA_DIR / "mcp_install.json"
        bundled_install = srv._PROJECT_ROOT / "mcp_install.json"
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

    return None
