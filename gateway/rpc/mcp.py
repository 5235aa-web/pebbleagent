"""MCP management RPCs."""

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid

    if method == "mcp.list":
        mcp_path = srv._DATA_DIR / ".mcp.json"
        bundled_mcp = srv._PROJECT_ROOT / ".mcp.json"
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
            pending = get_pending_project_mcp(str(srv._PROJECT_ROOT))
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
        server_cfg = data["mcpServers"].get(server_name)
        if not isinstance(server_cfg, dict):
            return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' not found"}}
        env = server_cfg.get("env")
        if not isinstance(env, dict):
            env = {}
        env[env_key] = value
        server_cfg["env"] = env
        srv._atomic_write_json(user_mcp_path, data)
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
        if not isinstance(entry, dict) or entry.get("type") != "download":
            return {"id": rid, "error": {"code": -32602, "message": f"server '{server_name}' is not a download-type server"}}
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
        # 3. 合并写入 ~/.pebble/.mcp.json（保留其他服务器）
        try:
            user_mcp_path = srv._DATA_DIR / ".mcp.json"
            bundled_mcp = srv._PROJECT_ROOT / ".mcp.json"
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
            srv._atomic_write_json(user_mcp_path, data)
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
