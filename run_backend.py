# -*- coding: utf-8 -*-
"""PebbleAgent GUI 后端启动入口"""
import sys, os, ctypes

# HIGH_PRIORITY_CLASS is a Windows-only API, and raising priority on POSIX needs
# root (negative nice), so the call is skipped there and normal scheduling stays.
if sys.platform == "win32":
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gateway.ws_server import run_server

if __name__ == "__main__":
    run_server()
