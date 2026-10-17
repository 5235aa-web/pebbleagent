"""
Behavior-based Network Request Guard V2

区别于 V1（禁止一切 HTTP 库），V2 基于行为模式检测：
- 允许单次可控的 HTTP 请求
- 阻止批量/遍历/自动化的爬虫行为

检测维度：
1. 循环遍历检测（for/while 遍历 URL 列表）
2. 翻页模式检测（page/offset/start 参数递增）
3. URL 提取+自动跟进检测
4. 递归/深度遍历检测
5. 速率限制（相同域名频率控制）
6. 爬虫框架检测（scrapy/selenium/playwright 等）
"""

import re
import time
import logging
from contextvars import ContextVar, Token
from typing import Tuple, List, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 守卫计数按会话隔离：每个工具执行任务在独立 context 中携带 scope，
# 会话之间不共享配额；未设置 scope 时回落到 "default"。
_GUARD_SCOPE: ContextVar[str] = ContextVar("pebble_guard_scope", default="")
_MAX_SCOPES = 512


def set_guard_scope(scope: str) -> Token:
    """设置当前执行任务的会话作用域，返回用于恢复的 token。"""
    return _GUARD_SCOPE.set(scope or "")


def reset_guard_scope(token: Token) -> None:
    _GUARD_SCOPE.reset(token)


def get_guard_scope() -> str:
    return _GUARD_SCOPE.get() or "default"


# ==================== 静态模式检测 ====================

# 遍历URL列表的关键模式
LOOP_TRAVERSAL_PATTERNS = [
    # for url in urls: requests.get(url)
    (r'\bfor\s+\w+\s+in\s+\w+.*(?:requests|urllib|httpx|aiohttp|fetch|axios|get|post)', "Loop over URLs with HTTP"),
    (r'\bwhile\s+.*(?:requests|urllib|httpx|aiohttp|fetch|axios)', "While loop with HTTP"),
    # 列表推导式中的HTTP调用
    (r'\[.*(?:requests|urllib|httpx|aiohttp|fetch|axios)\..*for\s+\w+\s+in', "List comprehension with HTTP"),
    # map + HTTP
    (r'\bmap\s*\(.*lambda.*(?:requests|urllib|httpx|aiohttp|fetch|axios)', "map() with HTTP"),
    # asyncio.gather + HTTP
    (r'\bgather\s*\(.*(?:requests|urllib|httpx|aiohttp|fetch)', "asyncio.gather with HTTP"),
]

# 翻页模式
PAGINATION_PATTERNS = [
    # page/offset/start 参数递增
    (r'(?:page|offset|start|skip|limit|per_page|p)\s*[+=]+\s*\d+', "Pagination parameter detected"),
    (r'(?:page|offset|start)\s*=\s*\d+.*(?:page|offset|start)\s*=\s*\d+', "Multiple pagination params"),
    # /page/1 /page/2 模式
    (r'f["\'].*/page/\d+', "URL path pagination"),
    (r'f["\'].*\?.*page=\{\}', "f-string pagination URL"),
    (r'f["\'].*\?.*offset=\{\}', "f-string offset URL"),
    (r'["\'].*\?.*page=["\']\s*\+', "String concat pagination URL"),
]

# HTTP 库导入检测 — 禁止在 execute_code 中使用 HTTP 库
# 所有网络请求必须通过 web_search / fetch_url / browser_navigate 工具
CODE_HTTP_LIB_PATTERNS = [
    # Python HTTP 库
    (r'\bimport\s+requests\b', "import requests - use web_search/fetch_url instead"),
    (r'\bfrom\s+requests\b', "from requests - use web_search/fetch_url instead"),
    (r'\bimport\s+httpx\b', "import httpx - use web_search/fetch_url instead"),
    (r'\bfrom\s+httpx\b', "from httpx - use web_search/fetch_url instead"),
    (r'\bimport\s+aiohttp\b', "import aiohttp - use web_search/fetch_url instead"),
    (r'\bfrom\s+aiohttp\b', "from aiohttp - use web_search/fetch_url instead"),
    (r'\bimport\s+urllib\b', "import urllib - use web_search/fetch_url instead"),
    (r'\bfrom\s+urllib\b', "from urllib - use web_search/fetch_url instead"),
    (r'\bimport\s+urllib3\b', "import urllib3 - use web_search/fetch_url instead"),
    (r'\bfrom\s+urllib3\b', "from urllib3 - use web_search/fetch_url instead"),
    (r'\bimport\s+urllib\.request\b', "import urllib.request - use web_search/fetch_url instead"),
    (r'\bfrom\s+urllib\.request\b', "from urllib.request - use web_search/fetch_url instead"),
    (r'\bimport\s+http\.client\b', "import http.client - use web_search/fetch_url instead"),
    (r'\bfrom\s+http\.client\b', "from http.client - use web_search/fetch_url instead"),
    # Shell 网络命令
    (r'\bcurl\s+', "curl - use web_search/fetch_url instead"),
    (r'\bwget\s+', "wget - use web_search/fetch_url instead"),
    (r'\bInvoke-WebRequest\b', "PowerShell Invoke-WebRequest - use web_search/fetch_url instead"),
    (r'\bInvoke-RestMethod\b', "PowerShell Invoke-RestMethod - use web_search/fetch_url instead"),
    # 动态导入绕过
    (r'\b__import__\s*\(\s*["\']requests["\']', "__import__('requests')"),
    (r'\b__import__\s*\(\s*["\']httpx["\']', "__import__('httpx')"),
    (r'\b__import__\s*\(\s*["\']urllib["\']', "__import__('urllib')"),
    (r'\b__import__\s*\(\s*["\']aiohttp["\']', "__import__('aiohttp')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']requests["\']', "importlib.import_module('requests')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']httpx["\']', "importlib.import_module('httpx')"),
]

# 爬虫框架检测（V1 保留）
CRAWLER_FRAMEWORK_PATTERNS = [
    (r'\bimport\s+scrapy\b', "import scrapy"),
    (r'\bfrom\s+scrapy\b', "from scrapy"),
    (r'\bimport\s+selenium\b', "import selenium"),
    (r'\bfrom\s+selenium\b', "from selenium"),
    (r'\bimport\s+playwright\b', "import playwright"),
    (r'\bfrom\s+playwright\b', "from playwright"),
    (r'\bimport\s+mechanicalsoup\b', "import mechanicalsoup"),
    (r'\bimport\s+bs4\b', "import bs4"),
    (r'\bfrom\s+bs4\b', "from bs4"),
    (r'\bCrawlSpider\b', "CrawlSpider"),
    (r'\bCrawlerProcess\b', "CrawlerProcess"),
    (r'\bSitemapSpider\b', "SitemapSpider"),
    # 动态导入绕过
    (r'\b__import__\s*\(\s*["\']scrapy["\']', "__import__('scrapy')"),
    (r'\b__import__\s*\(\s*["\']selenium["\']', "__import__('selenium')"),
    (r'\b__import__\s*\(\s*["\']playwright["\']', "__import__('playwright')"),
    (r'\bimportlib\.import_module\s*\(\s*["\']scrapy["\']', "importlib.import_module('scrapy')"),
]

# URL 提取+自动跟进检测
URL_EXTRACTION_PATTERNS = [
    # 提取所有链接后逐个访问
    (r'querySelectorAll\s*\(\s*["\']a["\']', "Extract all links"),
    (r'find_all\s*\(\s*["\']a["\']', "BeautifulSoup find_all links"),
    (r'getElementsByTagName\s*\(\s*["\']a["\']', "getElementsByTagName links"),
    # 递归爬取
    (r'\brecursive\b.*\bcrawl\b', "Recursive crawl"),
    (r'\bcrawl\b.*\brecursive\b', "Crawl recursive"),
    (r'\bdepth\s*[=:]\s*\d+', "Crawl depth parameter"),
    (r'\bmax_depth\s*[=:]\s*\d+', "Max depth parameter"),
]


def scan_code_for_scraping_behavior(code: str, language: str = "python") -> Tuple[bool, List[str]]:
    """
    扫描代码中是否包含爬虫行为模式
    
    Returns:
        (is_blocked, detected_patterns)
    """
    if not code or not code.strip():
        return False, []
    
    detected = []
    code_lower = code.lower()
    
    # 1. 检测 HTTP 库导入（封堵 execute_code 绕过）
    for pattern, desc in CODE_HTTP_LIB_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            detected.append(desc)
            break
    
    # 2. 检测循环遍历
    for pattern, desc in LOOP_TRAVERSAL_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            detected.append(desc)
            break
    
    # 3. 检测翻页
    for pattern, desc in PAGINATION_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            detected.append(desc)
            break
    
    # 4. 检测爬虫框架
    for pattern, desc in CRAWLER_FRAMEWORK_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            detected.append(desc)
            break
    
    # 5. 检测 URL 提取+自动跟进
    for pattern, desc in URL_EXTRACTION_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            detected.append(desc)
            break
    
    # 6. Shell 命令中的 curl/wget 循环
    if language in ("bash", "shell", "sh", "zsh", "powershell", "ps1", "pwsh"):
        if re.search(r'\bfor\s+\w+\s+in\s+.*\b(?:curl|wget)\b', code):
            detected.append("Shell loop with curl/wget")
        if re.search(r'\bwhile\s+.*\b(?:curl|wget)\b', code):
            detected.append("Shell while with curl/wget")
        if re.search(r'\bseq\s+\d+.*\b(?:curl|wget)\b', code):
            detected.append("seq loop with curl/wget")
    
    if detected:
        logger.warning(f"[NetworkGuardV2] Detected scraping behavior! Language={language}: {detected}")
        return True, detected
    
    return False, []


# ==================== 运行时速率限制 ====================

class RateLimiter:
    """
    速率限制器
    
    限制对相同域名的请求频率，防止爬虫行为。
    按域名追踪，每分钟最多 max_per_minute 次。
    """
    
    def __init__(self, max_per_minute: int = 6):
        self.max_per_minute = max_per_minute
        self._domain_hits: Dict[str, Dict[str, List[float]]] = {}

    def _bucket(self, scope: str) -> Dict[str, List[float]]:
        if scope not in self._domain_hits and len(self._domain_hits) >= _MAX_SCOPES:
            self._domain_hits.pop(next(iter(self._domain_hits)))
        return self._domain_hits.setdefault(scope, {})

    def check(self, url: str, scope: Optional[str] = None) -> Tuple[bool, str]:
        """
        检查 URL 是否在速率限制内
        
        Args:
            url: 目标 URL
            scope: 计数作用域（默认取当前会话）
        
        Returns:
            (allowed, message)
        """
        scope = scope or get_guard_scope()
        try:
            domain = urlparse(url).netloc
        except Exception:
            return True, ""
        
        if not domain:
            domain = "__non_url__"
        
        now = time.time()
        bucket = self._bucket(scope)
        hits = [t for t in bucket.get(domain, []) if now - t < 60]
        
        if len(hits) >= self.max_per_minute:
            wait = int(60 - (now - hits[0]))
            return False, f"Rate limited for domain '{domain}': max {self.max_per_minute} requests/minute, retry in {wait}s"
        
        hits.append(now)
        bucket[domain] = hits
        return True, ""
    
    def reset(self, scope: Optional[str] = None):
        """重置速率限制计数；scope 为空时重置全部作用域。"""
        if scope is None:
            self._domain_hits.clear()
        else:
            self._domain_hits.pop(scope, None)


# ==================== URL 路径深度限制 ====================

class PathDepthGuard:
    """
    路径深度限制器
    
    防止 agent 遍历 URL 路径（如 /page/1, /page/2, /page/3）
    检测翻页模式 URL
    """
    
    _PAGINATION_PATTERNS = re.compile(
        r'(/page/\d+|/p/\d+|\?page=\d+|&page=\d+|\?p=\d+|&p=\d+'
        r'|\?offset=\d+|&offset=\d+|\?start=\d+|&start=\d+'
        r'|\?skip=\d+|&skip=\d+|\?limit=\d+|&limit=\d+)',
        re.IGNORECASE
    )
    
    def __init__(self, max_navigations_per_turn: int = 3):
        self.max_per_turn = max_navigations_per_turn
        self._state: Dict[str, Tuple[int, Dict[str, str]]] = {}

    def _scope_state(self, scope: str) -> Tuple[int, Dict[str, str]]:
        if scope not in self._state and len(self._state) >= _MAX_SCOPES:
            self._state.pop(next(iter(self._state)))
        return self._state.get(scope, (0, {}))
    
    def check_navigate(self, url: str, scope: Optional[str] = None) -> Tuple[bool, str]:
        """
        检查导航是否允许
        
        Args:
            url: 目标 URL
            scope: 计数作用域（默认取当前会话）
        
        Returns:
            (allowed, message)
        """
        scope = scope or get_guard_scope()
        count, patterns = self._scope_state(scope)
        
        # 检查翻页模式
        if self._PAGINATION_PATTERNS.search(url):
            base = self._PAGINATION_PATTERNS.sub("", url)
            if base in patterns:
                return False, f"Pagination pattern detected for '{base}'. Multi-page traversal is prohibited."
            patterns[base] = url
        
        # 检查导航次数
        count += 1
        self._state[scope] = (count, patterns)
        if count > self.max_per_turn:
            return False, f"Navigation limit exceeded: max {self.max_per_turn} navigations per turn"
        
        return True, ""
    
    def reset(self, scope: Optional[str] = None):
        """重置导航计数；scope 为空时重置全部作用域。"""
        if scope is None:
            self._state.clear()
        else:
            self._state.pop(scope, None)


# ==================== 下载限制 ====================

# 允许下载的文件类型
ALLOWED_DOWNLOAD_EXTENSIONS = {
    # 文档类
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.odt', '.ods', '.odp', '.rtf', '.md', '.markdown',
    '.txt', '.csv', '.tsv', '.log',
    '.json', '.xml', '.yaml', '.yml', '.toml', '.ini', '.cfg',
    # 图片类
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
    '.bmp', '.tiff', '.tif', '.heic', '.heif',
    # 压缩包类
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.zst',
    '.7z', '.rar',
    # 音频类
    '.mp3', '.wav', '.flac', '.ogg', '.aac', '.wma', '.m4a',
    # 视频类
    '.mp4', '.webm', '.avi', '.mkv', '.mov', '.wmv', '.flv',
    # 电子书类
    '.epub', '.mobi', '.azw', '.azw3', '.fb2',
    # 代码/数据类
    '.ipynb', '.sql', '.sqlite', '.db',
    '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.h',
    '.css', '.scss', '.less',
    '.sh', '.bat', '.ps1',
    # 字体类
    '.ttf', '.otf', '.woff', '.woff2', '.eot',
    # 其他
    '.iso', '.dmg',
}

# 禁止下载的网页文件类型
BLOCKED_DOWNLOAD_EXTENSIONS = {
    '.html', '.htm', '.php', '.asp', '.aspx', '.jsp', '.cfm',
}


class DownloadGuard:
    """下载限制器"""
    
    def __init__(self, max_files_per_turn: int = 2, max_size_mb: int = 50):
        self.max_files = max_files_per_turn
        self.max_size = max_size_mb * 1024 * 1024
        self._counts: Dict[str, int] = {}

    def _scope_count(self, scope: str) -> int:
        if scope not in self._counts and len(self._counts) >= _MAX_SCOPES:
            self._counts.pop(next(iter(self._counts)))
        return self._counts.get(scope, 0)
    
    def check_download(self, url: str, filename: str = "", scope: Optional[str] = None) -> Tuple[bool, str]:
        """
        检查下载是否允许
        
        Args:
            url: 下载地址
            filename: 目标文件名
            scope: 计数作用域（默认取当前会话）
        
        Returns:
            (allowed, message)
        """
        scope = scope or get_guard_scope()
        count = self._scope_count(scope)
        
        # 检查下载次数
        if count >= self.max_files:
            return False, f"Download limit exceeded: max {self.max_files} files per turn"
        
        # 检查文件扩展名
        ext = self._get_extension(url, filename)
        if ext:
            ext_lower = ext.lower()
            if ext_lower in BLOCKED_DOWNLOAD_EXTENSIONS:
                return False, f"Downloading '{ext}' files is prohibited (web page files cannot be downloaded)"
            if ext_lower not in ALLOWED_DOWNLOAD_EXTENSIONS and ext_lower:
                return False, f"Downloading '{ext}' files is not allowed. Allowed: {', '.join(sorted(ALLOWED_DOWNLOAD_EXTENSIONS))}"
        
        self._counts[scope] = count + 1
        return True, ""
    
    def _get_extension(self, url: str, filename: str) -> str:
        name = filename or url.split('?')[0].split('/')[-1]
        if '.' in name:
            return '.' + name.rsplit('.', 1)[1]
        return ""
    
    def reset(self, scope: Optional[str] = None):
        """重置下载计数；scope 为空时重置全部作用域。"""
        if scope is None:
            self._counts.clear()
        else:
            self._counts.pop(scope, None)


# ==================== 全局单例 ====================

_rate_limiter = RateLimiter()
_path_depth_guard = PathDepthGuard()
_download_guard = DownloadGuard()


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter


def get_path_depth_guard() -> PathDepthGuard:
    return _path_depth_guard


def get_download_guard() -> DownloadGuard:
    return _download_guard


def reset_all_guards(scope: Optional[str] = None):
    """重置守卫计数；scope 为空时重置全部作用域。"""
    _rate_limiter.reset(scope)
    _path_depth_guard.reset(scope)
    _download_guard.reset(scope)
