# -*- coding: utf-8 -*-
"""统一日志配置：stderr（控制台）+ 轮转文件（可事后追查）。

2026-10-15 新增：后端日志此前仅输出到 stderr（cmd 窗口），进程重启后无法
追查历史（"过程突然终止"事件调查因此缺失最直接的证据）。本模块将日志同时
写入 {PEBBLE_HOME}/logs/backend.log（5MB × 5 轮转，UTF-8）。
"""

import logging
import logging.handlers
import os
from pathlib import Path

_STDERR_FORMAT = "%(levelname)s:%(name)s:%(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(message)s"


def setup_logging() -> None:
    """配置根日志器：stderr + 轮转文件。可重复调用（幂等）。

    文件日志失败（如目录不可写）时静默降级——stderr 输出始终保留。
    """
    root = logging.getLogger()

    # 幂等：已配置过（带标记的 handler 存在）则跳过
    for h in root.handlers:
        if getattr(h, "_pebble_log_handler", False):
            return

    level = getattr(logging, os.environ.get("PEBBLE_LOG_LEVEL", "INFO").upper(), logging.INFO)
    root.setLevel(level)

    # stderr（控制台，保持原行为）
    sh = logging.StreamHandler()  # 默认 stderr
    sh.setFormatter(logging.Formatter(_STDERR_FORMAT))
    sh._pebble_log_handler = True  # type: ignore[attr-defined]
    root.addHandler(sh)

    # 轮转文件日志（可事后追查）
    try:
        home = os.environ.get("PEBBLE_HOME") or os.path.join(str(Path.home()), ".pebble")
        log_dir = os.path.join(home, "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "backend.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(_FILE_FORMAT))
        fh._pebble_log_handler = True  # type: ignore[attr-defined]
        root.addHandler(fh)
    except Exception:  # 文件日志不可用时静默降级（stderr 仍可用）
        pass

