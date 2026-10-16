"""
代码网络请求防护模块

在代码执行前进行静态扫描，检测并拦截所有可能的 HTTP/网络请求行为。
这是除系统提示禁止说明之外的硬编码安全机制。

检测范围：
- Python: requests, urllib, httpx, aiohttp, urllib3, httplib, http.client, socket 网络调用
- Shell: curl, wget, nc, netcat 等网络命令
- JavaScript: fetch, XMLHttpRequest, axios, node-http 等

严格约束：只要检测到网络请求意图，立即终止执行，不可绕过。
"""

import re
import logging
from typing import Tuple, List

logger = logging.getLogger(__name__)


# === Python 网络请求检测模式 ===
# 匹配 import 语句和直接调用
PYTHON_NETWORK_PATTERNS = [
    # --- import 语句 ---
    (r'\bimport\s+requests\b', "import requests"),
    (r'\bfrom\s+requests\b', "from requests"),
    (r'\bimport\s+urllib\b', "import urllib"),
    (r'\bfrom\s+urllib\b', "from urllib"),
    (r'\bimport\s+httpx\b', "import httpx"),
    (r'\bfrom\s+httpx\b', "from httpx"),
    (r'\bimport\s+aiohttp\b', "import aiohttp"),
    (r'\bfrom\s+aiohttp\b', "from aiohttp"),
    (r'\bimport\s+urllib3\b', "import urllib3"),
    (r'\bfrom\s+urllib3\b', "from urllib3"),
    (r'\bimport\s+httplib\b', "import httplib"),
    (r'\bfrom\s+httplib\b', "from httplib"),
    (r'\bimport\s+http\.client\b', "import http.client"),
    (r'\bfrom\s+http\.client\b', "from http.client"),
    (r'\bimport\s+socket\b', "import socket"),
    (r'\bfrom\s+socket\b', "from socket"),
    (r'\bimport\s+websockets\b', "import websockets"),
    (r'\bfrom\s+websockets\b', "from websockets"),
    (r'\bimport\s+selenium\b', "import selenium"),
    (r'\bfrom\s+selenium\b', "from selenium"),
    (r'\bimport\s+playwright\b', "import playwright"),
    (r'\bfrom\s+playwright\b', "from playwright"),
    (r'\bimport\s+mechanicalsoup\b', "import mechanicalsoup"),
    (r'\bfrom\s+mechanicalsoup\b', "from mechanicalsoup"),
    (r'\bimport\s+beautifulsoup4\b', "import beautifulsoup4"),
    (r'\bfrom\s+beautifulsoup4\b', "from beautifulsoup4"),
    (r'\bimport\s+bs4\b', "import bs4"),
    (r'\bfrom\s+bs4\b', "from bs4"),
    (r'\bimport\s+scrapy\b', "import scrapy"),
    (r'\bfrom\s+scrapy\b', "from scrapy"),
    
    # --- 直接调用 ---
    (r'\brequests\.\s*(get|post|put|delete|patch|head|options|request)\s*\(', "requests.get/post/..."),
    (r'\burllib\.request\.urlopen\s*\(', "urllib.request.urlopen"),
    (r'\burlopen\s*\(', "urlopen()"),
    (r'\bhttpx\.\s*(get|post|put|delete|patch|head|options|request)\s*\(', "httpx.get/post/..."),
    (r'\baiohttp\.ClientSession\s*\(', "aiohttp.ClientSession"),
    (r'\bsocket\.socket\s*\(', "socket.socket()"),
    (r'\bsocket\.connect\s*\(', "socket.connect()"),
    (r'\bHTTPlib\.HTTPConnection\s*\(', "httplib.HTTPConnection"),
    (r'\bhttp\.client\.HTTPConnection\s*\(', "http.client.HTTPConnection"),
    
    # --- Selenium / Playwright ---
    (r'\bwebdriver\s*\(', "webdriver()"),
    (r'\bchromium\.launch\s*\(', "chromium.launch()"),
    (r'\bbrowser\.new_page\s*\(', "browser.new_page()"),
    (r'\bbrowser\.new_context\s*\(', "browser.new_context()"),
    (r'\bpage\.goto\s*\(', "page.goto()"),
    (r'\bpage\.navigate\s*\(', "page.navigate()"),
    
    # --- LLM 常见绕过尝试 ---
    # __import__ 动态导入网络库
    (r'\b__import__\s*\(\s*["\']requests["\']', "__import__('requests')"),
    (r'\b__import__\s*\(\s*["\']urllib["\']', "__import__('urllib')"),
    (r'\b__import__\s*\(\s*["\']httpx["\']', "__import__('httpx')"),
    (r'\b__import__\s*\(\s*["\']aiohttp["\']', "__import__('aiohttp')"),
    (r'\b__import__\s*\(\s*["\']socket["\']', "__import__('socket')"),
    (r'\b__import__\s*\(\s*["\']httplib["\']', "__import__('httplib')"),
    (r'\b__import__\s*\(\s*["\']http\.client["\']', "__import__('http.client')"),
    (r'\b__import__\s*\(\s*["\']selenium["\']', "__import__('selenium')"),
    (r'\b__import__\s*\(\s*["\']playwright["\']', "__import__('playwright')"),
    # importlib 动态导入
    (r'\bimportlib\.import_module\s*\(\s*["\']requests["\']', "importlib.import_module('requests')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']urllib["\']', "importlib.import_module('urllib')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']httpx["\']', "importlib.import_module('httpx')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']aiohttp["\']', "importlib.import_module('aiohttp')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']socket["\']', "importlib.import_module('socket')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']selenium["\']', "importlib.import_module('selenium')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']playwright["\']', "importlib.import_module('playwright')"),
    # exec/eval + 网络关键词（LLM 常见绕过：把网络请求藏进 exec/eval 字符串）
    (r'\bexec\s*\(.*(?:requests|urllib|httpx|aiohttp|selenium|playwright|socket)', "exec() with network lib"),
    (r'\beval\s*\(.*(?:requests|urllib|httpx|aiohttp|selenium|playwright|socket)', "eval() with network lib"),
    # getattr 动态获取网络库方法
    (r'\bgetattr\s*\(\s*(?:requests|urllib|httpx|aiohttp|socket)', "getattr() on network lib"),
    # globals/locals 动态注入网络库
    (r'\bglobals\s*\(\s*\)\s*\[.*(?:requests|urllib|httpx|aiohttp|socket)', "globals() with network lib"),
    # 字符串拼接构建网络库名（LLM 常见：mod='requ'+'ests'）
    (r'["\']requ["\']\s*\+\s*["\']ests["\']', "string concat: 'requ'+'ests'"),
    (r'["\']urllib["\']\s*\+\s*["\']', "string concat: 'urllib'+..."),
    (r'["\']httpx["\']\s*\+\s*["\']', "string concat: 'httpx'+..."),
    (r'["\']aiohttp["\']\s*\+\s*["\']', "string concat: 'aiohttp'+..."),
    (r'["\']socket["\']\s*\+\s*["\']', "string concat: 'socket'+..."),
]

# === Shell 网络请求检测模式 ===
SHELL_NETWORK_PATTERNS = [
    (r'\bcurl\s+', "curl"),
    (r'\bwget\s+', "wget"),
    (r'\bnc\s+', "nc (netcat)"),
    (r'\bnetcat\s+', "netcat"),
    (r'\btelnet\s+', "telnet"),
    (r'\bssh\s+', "ssh"),
    (r'\bscp\s+', "scp"),
    (r'\brsync\s+', "rsync"),
    (r'\blynx\s+', "lynx"),
    (r'\bpython\s+.*urllib', "python urllib via shell"),
    (r'\bpython\s+.*requests', "python requests via shell"),
    (r'\bpython\s+-c\s+.*import\s+requests', "python -c import requests"),
    (r'\bpython\s+-c\s+.*import\s+urllib', "python -c import urllib"),
    (r'\bpip\s+install\s+requests', "pip install requests"),
    (r'\bpip\s+install\s+httpx', "pip install httpx"),
    (r'\bpip\s+install\s+aiohttp', "pip install aiohttp"),
    (r'\bpip\s+install\s+selenium', "pip install selenium"),
    (r'\bpip\s+install\s+scrapy', "pip install scrapy"),
    (r'\bpip\s+install\s+beautifulsoup', "pip install beautifulsoup"),
    (r'\bnpm\s+install\s+axios', "npm install axios"),
    (r'\bnpm\s+install\s+node-fetch', "npm install node-fetch"),
    (r'\bInvoke-WebRequest\b', "PowerShell Invoke-WebRequest"),
    (r'\bInvoke-RestMethod\b', "PowerShell Invoke-RestMethod"),
    (r'\bNew-Object\s+System\.Net\.WebClient\b', "PowerShell WebClient"),
    (r'\bNew-Object\s+System\.Net\.HttpClient\b', "PowerShell HttpClient"),
]

# === JavaScript 网络请求检测模式 ===
JS_NETWORK_PATTERNS = [
    (r'\bfetch\s*\(', "fetch()"),
    (r'\bXMLHttpRequest\s*\(', "XMLHttpRequest"),
    (r'\bnew\s+XMLHttpRequest\b', "new XMLHttpRequest"),
    (r'\baxios\.\s*(get|post|put|delete|patch|head|request)\s*\(', "axios.get/post/..."),
    (r'\brequire\s*\(\s*["\']http["\']\s*\)', "require('http')"),
    (r'\brequire\s*\(\s*["\']https["\']\s*\)', "require('https')"),
    (r'\brequire\s*\(\s*["\']node-fetch["\']\s*\)', "require('node-fetch')"),
    (r'\brequire\s*\(\s*["\']axios["\']\s*\)', "require('axios')"),
    (r'\brequire\s*\(\s*["\']request["\']\s*\)', "require('request')"),
    (r'\brequire\s*\(\s*["\']got["\']\s*\)', "require('got')"),
    (r'\brequire\s*\(\s*["\']superagent["\']\s*\)', "require('superagent')"),
    (r'\bimport\s+.*from\s+["\']node-fetch["\']', "import from node-fetch"),
    (r'\bimport\s+.*from\s+["\']axios["\']', "import from axios"),
    (r'\bhttp\.request\s*\(', "http.request()"),
    (r'\bhttps\.request\s*\(', "https.request()"),
]


def scan_code_for_network_requests(code: str, language: str = "python") -> Tuple[bool, List[str]]:
    """
    扫描代码中是否包含网络请求行为
    
    Args:
        code: 要检查的代码字符串
        language: 编程语言
    
    Returns:
        (is_blocked, detected_patterns): 
            is_blocked: True 表示检测到网络请求，应阻止执行
            detected_patterns: 检测到的具体模式列表
    """
    if not code or not code.strip():
        return False, []
    
    detected = []
    language_lower = language.lower().strip()
    
    # 选择对应的检测模式
    patterns = []
    if language_lower in ("python", "py"):
        patterns = PYTHON_NETWORK_PATTERNS
    elif language_lower in ("bash", "shell", "sh", "zsh", "powershell", "pwsh"):
        patterns = SHELL_NETWORK_PATTERNS
    elif language_lower in ("javascript", "js", "node", "nodejs"):
        patterns = JS_NETWORK_PATTERNS
    elif language_lower in ("ruby", "rb"):
        # Ruby 也需要检测
        patterns = [
            (r'\brequire\s+["\']net/http["\']', "require 'net/http'"),
            (r'\brequire\s+["\']open-uri["\']', "require 'open-uri'"),
            (r'\brequire\s+["\']httparty["\']', "require 'httparty'"),
            (r'\brequire\s+["\']rest-client["\']', "require 'rest-client'"),
            (r'\bNet::HTTP\.', "Net::HTTP"),
            (r'\bURI\.open\s*\(', "URI.open()"),
            (r'\bopen\s*\(\s*["\']https?://', "open('http://...')"),
        ]
    else:
        # 未知语言，对所有模式都检查一遍
        patterns = PYTHON_NETWORK_PATTERNS + SHELL_NETWORK_PATTERNS + JS_NETWORK_PATTERNS
    
    # 对 Python 代码也额外检查 shell 命令（os.system, subprocess 等）
    if language_lower in ("python", "py"):
        extra_shell_in_python = [
            (r'\bos\.system\s*\(.*curl\b', "os.system('curl...')"),
            (r'\bos\.system\s*\(.*wget\b', "os.system('wget...')"),
            (r'\bos\.popen\s*\(.*curl\b', "os.popen('curl...')"),
            (r'\bos\.popen\s*\(.*wget\b', "os.popen('wget...')"),
            (r'\bsubprocess\.\s*(run|call|Popen|check_output|check_call)\s*\(.*curl\b', "subprocess with curl"),
            (r'\bsubprocess\.\s*(run|call|Popen|check_output|check_call)\s*\(.*wget\b', "subprocess with wget"),
            (r'\bsubprocess\.\s*(run|call|Popen|check_output|check_call)\s*\(.*requests\b', "subprocess with requests"),
        ]
        patterns = list(patterns) + extra_shell_in_python
    
    # 执行扫描
    code_lines = code.split('\n')
    for pattern, description in patterns:
        try:
            # 逐行检查，忽略注释行
            for i, line in enumerate(code_lines):
                stripped = line.strip()
                # 跳过纯注释行（Python: #, Shell: #, JS: //）
                if stripped.startswith('#') or stripped.startswith('//'):
                    continue
                if re.search(pattern, line):
                    detected.append(f"行{i+1}: {description}")
                    break  # 每个模式只需匹配一次
        except re.error:
            # 正则表达式错误，跳过此模式
            continue
    
    if detected:
        logger.warning(f"[NetworkGuard] 检测到网络请求行为! 语言={language}, 检测到: {detected}")
        return True, detected
    
    return False, []


def check_and_block_network_code(code: str, language: str = "python") -> Tuple[bool, str]:
    """
    检查代码是否包含网络请求，如果包含则返回阻止消息
    
    Args:
        code: 要检查的代码字符串
        language: 编程语言
    
    Returns:
        (is_allowed, message):
            is_allowed: True 表示代码安全，可以执行
            message: 如果被阻止，包含阻止原因；如果允许，为空字符串
    """
    is_blocked, detected = scan_code_for_network_requests(code, language)
    
    if is_blocked:
        detected_str = "; ".join(detected)
        message = (
            f"⛔ [安全拦截] 代码中检测到网络请求行为，已被阻止执行。\n"
            f"检测到: {detected_str}\n"
            f"说明: 禁止通过代码发起任何HTTP/网络请求。如需搜索网页，请使用 web_search 工具。"
        )
        logger.warning(f"[NetworkGuard] 代码执行被拦截: {detected_str}")
        return False, message
    
    return True, ""
