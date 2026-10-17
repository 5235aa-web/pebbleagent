"""Network fetch, search and browser tools."""

import asyncio
import logging
import os
import re
from typing import List

import httpx

from ..truncation_manager import truncate_output
from ..code_network_guardV2 import (
    get_rate_limiter,
    get_path_depth_guard,
    get_download_guard,
)

logger = logging.getLogger(__name__)



def _sanitize_download_filename(raw: str) -> str:
    """净化下载文件名，返回安全的相对路径（'/' 分隔，可含子目录）；非法输入返回 ""。

    拒绝：空、'.'/'..' 段、含 ':' 的段（盘符/ADS）、控制字符、Windows 保留设备名、
    前导 '/'（Unix 绝对路径/UNC）。
    """
    name = (raw or "").strip().replace("\\", "/")
    if not name or name.startswith("/"):
        return ""
    parts: List[str] = []
    for seg in name.split("/"):
        if seg in ("", "."):
            continue
        if seg == ".." or ":" in seg:
            return ""
        if any(ord(ch) < 32 for ch in seg):
            return ""
        stem = seg.split(".")[0].strip().upper()
        if stem in _WINDOWS_RESERVED_STEMS:
            return ""
        parts.append(seg)
    return "/".join(parts)


_WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _parse_bing_search_results(html: str) -> list:
    """
    从 Bing 搜索结果页 HTML 中提取结构化结果。
    
    多层策略递进（Bing 不定期改 HTML，多层次兜底确保解析成功率）：
    0. html.parser 标准库 DOM 解析（韧性最强，不依赖 class 名）
    1. <li class="b_algo"> 结构（经典 Bing）
    2. <li class="b_ans"> 结构（Bing 答案卡片）
    3. 通用链接提取（tilk 类 / 任意 <a> 过滤）
    4. 文本正则回退（从纯文本中提取「标题 - 描述」对）
    """
    from html.parser import HTMLParser
    results = []
    seen_urls = set()
    
    def add_result(url: str, title: str, snippet: str = ""):
        nonlocal seen_urls
        if not url or not title:
            return
        # 清理 Bing 追踪包装 URL
        url = re.sub(r'^/url\?q=', '', url)
        url = url.split('&')[0] if '&' in url else url
        url = re.sub(r'^https?://[^/]+/url\?q=', '', url)
        url = url.split('&')[0] if '&' in url else url
        # 去重
        norm_url = url.rstrip('/').lower()
        if norm_url in seen_urls:
            return
        # 过滤内部/无用链接
        for skip in ["bing.com", "microsoft.com", "msn.com", "live.com",
                      "javascript:", "#", "go.microsoft.com"]:
            if skip in norm_url:
                return
        # 跳过已知广告/跟踪域名
        for ad in ["doubleclick.net", "googleadservices.com", "googlesyndication.com"]:
            if ad in norm_url:
                return
        seen_urls.add(norm_url)
        title = re.sub(r'\s+', ' ', title).strip()
        snippet = re.sub(r'\s+', ' ', snippet).strip() if snippet else ""
        results.append({"title": title, "url": url, "snippet": snippet})
    
    def strip_html(text: str) -> str:
        return re.sub(r'<[^>]+>', '', text).strip()
    
    # === Strategy 0: html.parser 标准库 DOM 解析 ===
    # 不依赖特定 class 名，韧性最高
    if not results:
        class BingResultParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self._in_link = False
                self._in_p = False
                self._current_tag = None
                self._current_link_href = ""
                self._current_link_text = ""
                self._current_snippet = ""
                self._parse_link = False
                self._tag_stack = []
            
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                self._tag_stack.append(tag)
                
                is_result_link = (tag == 'a' and 'href' in attrs_dict)
                if is_result_link:
                    href = attrs_dict['href']
                    if href.startswith(('http://', 'https://', '/url?q=')):
                        self._in_link = True
                        self._current_link_href = href
                        self._current_link_text = ""
                        self._current_snippet = ""
                
                if tag == 'p':
                    self._in_p = True
                    self._current_snippet = ""
            
            def handle_data(self, data):
                if self._in_link:
                    self._current_link_text += data
                if self._in_p:
                    self._current_snippet += data
            
            def handle_endtag(self, tag):
                self._tag_stack.pop()
                if tag == 'a' and self._in_link:
                    self._in_link = False
                    # 仅在链接文本足够长时才认为是搜索结果
                    text = self._current_link_text.strip()
                    href = self._current_link_href
                    if text and len(text) > 5:
                        # 检查父级是否在 b_algo 或 b_ans 内（有摘要提示更可靠）
                        add_result(href, text)
                if tag == 'p':
                    self._in_p = False
        
        parser = BingResultParser()
        try:
            parser.feed(html)
        except Exception:
            pass  # HTML 解析失败，继续下个策略
    
    # === Strategy 1: <li class="b_algo"> (经典 Bing) ===
    if not results:
        algo_blocks = re.findall(
            r'<li[^>]*class="b_algo"[^>]*>(.*?)</li>',
            html, re.DOTALL | re.IGNORECASE
        )
        for block in algo_blocks:
            link_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_match:
                link_match = re.search(r'<a[^>]*href="(/url\?q=[^"&]+)[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_match:
                continue
            url = link_match.group(1)
            title = strip_html(link_match.group(2))
            snippet = ""
            p_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if p_match:
                snippet = strip_html(p_match.group(1))
            if not snippet:
                caption_match = re.search(r'<div[^>]*class="b_caption"[^>]*>(.*?)</div>', block, re.DOTALL)
                if caption_match:
                    snippet = strip_html(caption_match.group(1))
            if not snippet:
                snippet_match = re.search(r'<span[^>]*class="b_snip"[^>]*>(.*?)</span>', block, re.DOTALL)
                if snippet_match:
                    snippet = strip_html(snippet_match.group(1))
            add_result(url, title, snippet)
            if len(results) >= 15:
                break
    
    # === Strategy 2: <li class="b_ans"> (Bing 答案卡片) ===
    if not results:
        ans_blocks = re.findall(
            r'<li[^>]*class="b_ans"[^>]*>(.*?)</li>',
            html, re.DOTALL | re.IGNORECASE
        )
        for block in ans_blocks:
            link_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if link_match:
                add_result(link_match.group(1), strip_html(link_match.group(2)))
    
    # === Strategy 3: 通用链接提取（适用于新版 Bing UI） ===
    if not results:
        all_links = re.findall(
            r'<a[^>]*href="(https?://(?!www\.bing\.com|www\.microsoft\.com|www\.msn\.com)[^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for url, title_html in all_links[:30]:
            title = strip_html(title_html)
            if title and len(title) > 5:
                add_result(url, title)
                if len(results) >= 15:
                    break
    
    # === Strategy 4: 从纯文本用正则回退 ===
    if not results:
        text = re.sub(r'<[^>]+>', '\n', html)
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        i = 0
        while i < len(lines) - 1 and len(results) < 15:
            line = lines[i]
            url_match = re.match(r'(https?://[^\s]+)', line)
            if url_match:
                url = url_match.group(1)
                title = line[len(url):].strip()
                snippet = lines[i + 1] if i + 1 < len(lines) else ""
                if title and len(title) > 3:
                    add_result(url, title, snippet)
                i += 2
            else:
                i += 1
    
    return results


class WebToolsMixin:

    async def _tool_fetch_url(self, url: str, timeout: int = 30) -> str:
        """获取网页内容"""
        if not url:
            return "Error: No URL provided"
        
        # 第一阶段：httpx 快速获取
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                raw_text = resp.text
        except Exception as e:
            return f"Error: Failed to fetch URL: {e}"
        
        return truncate_output(raw_text)[0]


    async def _tool_web_search(self, query: str) -> str:
        """
        搜索网络（通过 httpx 抓取 Bing 搜索页并解析结构化结果）
        
        受 code_network_guardV2 的速率限制保护。
        """
        if not query:
            return "Error: No query provided"
        
        # 速率限制检查
        rate_limiter = get_rate_limiter()
        allowed, msg = rate_limiter.check(query)
        if not allowed:
            return f"Error: {msg}"
        
        try:
            # 通过 httpx 抓取 Bing 搜索页
            from urllib.parse import quote
            search_url = f"https://cn.bing.com/search?q={quote(query)}&count=8"
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(
                    search_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
            # 解析 Bing 搜索结果 HTML，提取结构化结果
            parsed_results = _parse_bing_search_results(resp.text)
            result = {
                "content": {
                    "text": resp.text,
                    "url": str(resp.url),
                    "title": "",
                    "links": [
                        {"href": r["url"], "text": r["title"]}
                        for r in parsed_results
                    ],
                    "_parsed_results": parsed_results,
                }
            }
            
            content = result.get("content")
            if not content:
                return f"Web search for: {query}\nNo results returned from search."
            
            # 提取搜索结果文本
            text = content.get("text", "")
            links = content.get("links", [])
            url = content.get("url", "")
            
            # 优先使用已解析的结构化结果（httpx 路径有 _parsed_results；否则从 links 提取）
            parsed_results = content.get("_parsed_results", [])
            if not parsed_results and links:
                for link in links:
                    href = link.get("href", "")
                    if href and not any(ignore in href for ignore in ["bing.com", "microsoft.com", "#", "javascript:"]):
                        parsed_results.append({
                            "title": link.get("text", "").strip(),
                            "url": href,
                            "snippet": "",
                        })
            
            # 格式化输出
            output_parts = [f"## Search Results: {query}", f"Search page: {url}\n"]
            
            if parsed_results:
                output_parts.append(f"Found {len(parsed_results)} results:")
                for i, r in enumerate(parsed_results[:15], 1):
                    title = r.get("title", "").strip()
                    snippet = r.get("snippet", "").strip()
                    result_url = r.get("url", "")
                    output_parts.append("")
                    output_parts.append(f"{i}. **{title}**")
                    if snippet:
                        output_parts.append(f"   {snippet}")
                    output_parts.append(f"   {result_url}")
                output_parts.append("")
            
            # 附上原始文本（限长，无结构化结果时的备用）
            if text and not parsed_results:
                truncated, _ = truncate_output(text)
                output_parts.append(f"### Page Content (raw):\n{truncated}")
            
            return "\n".join(output_parts)
            
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[ToolExecutor] Search error: {e}")
            return f"Error: Search failed - {str(e)}"


    async def _tool_browser_navigate(self, url: str, extract_links: bool = False) -> str:
        """
        导航到指定 URL 并获取页面内容
        
        受 path_depth_guard 限制器保护（翻页检测 + 每轮次数限制）。
        """
        if not url:
            return "Error: No URL provided"
        
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"
        
        # 路径深度检查（翻页检测 + 每轮导航次数）
        path_guard = get_path_depth_guard()
        allowed, msg = path_guard.check_navigate(url)
        if not allowed:
            return f"Error: {msg}"
        
        # 速率限制
        rate_limiter = get_rate_limiter()
        allowed, msg = rate_limiter.check(url)
        if not allowed:
            return f"Error: {msg}"
        
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
            raw = resp.text
            import re
            text_only = re.sub(r'<[^>]+>', '\n', raw)
            lines = [l.strip() for l in text_only.split('\n') if l.strip()]
            clean_text = '\n'.join(lines[:500])
            truncated, _ = truncate_output(clean_text)
            output_parts = [
                f"## URL: {url}",
                "(Fetched via httpx)",
                "",
                "### Page Content:",
                truncated
            ]
            return "\n".join(output_parts)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[ToolExecutor] Navigate error: {e}")
            return f"Error: Failed to fetch URL: {e}"


    async def _tool_browser_download(self, url: str, filename: str = "") -> str:
        """
        通过 httpx 下载文件
        
        文件直接保存到 agent 工作区文件夹。
        受 download_guard 限制器保护（每轮文件数 + 文件类型 + 大小）。
        """
        if not url:
            return "Error: No URL provided"
        
        # 下载限制检查
        download_guard = get_download_guard()
        allowed, msg = download_guard.check_download(url, filename)
        if not allowed:
            return f"Error: {msg}"
        
        # 解析保存路径：工作区文件夹
        from urllib.parse import urlparse, unquote
        if not filename:
            parsed = urlparse(url)
            path_part = unquote(parsed.path.split('/')[-1] or 'download')
            filename = path_part if path_part else 'download'
        
        # 净化文件名（拒绝路径逃逸 / 非法字符 / Windows 保留名；允许安全子目录）
        safe_name = _sanitize_download_filename(filename)
        if not safe_name:
            return f"Error: Invalid filename: {filename!r}"
        
        save_path = os.path.join(self._current_project_path, safe_name)
        # 边界兜底校验（realpath/commonpath，与 PathBoundaryChecker 一致）
        from ..tool_permission import PathBoundaryChecker
        allowed_path, path_reason = PathBoundaryChecker.check_path_boundary(
            save_path, self._current_project_path
        )
        if not allowed_path:
            return f"Error: Download path rejected: {path_reason}"
        
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(url)
            # 根据 Content-Type 推断扩展名
            content_type = resp.headers.get("content-type", "")
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "image/webp": ".webp", "image/svg+xml": ".svg",
                "application/pdf": ".pdf", "application/zip": ".zip",
                "text/html": ".html", "text/plain": ".txt",
                "application/json": ".json",
            }
            if not os.path.splitext(safe_name)[1]:
                for mime, ext in ext_map.items():
                    if mime in content_type:
                        safe_name += ext
                        break
                else:
                    safe_name += ".bin"
                save_path = os.path.join(self._current_project_path, safe_name)
                # 防御性复检（追加扩展名不改变目录，理论安全；保留兜底）
                allowed_path2, path_reason2 = PathBoundaryChecker.check_path_boundary(
                    save_path, self._current_project_path
                )
                if not allowed_path2:
                    return f"Error: Download path rejected: {path_reason2}"
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            with open(save_path, 'wb') as f:
                f.write(resp.content)
            file_size = len(resp.content)
            return (
                f"Download completed.\n"
                f"File: {safe_name}\n"
                f"URL: {url}\n"
                f"Save path: {save_path}\n"
                f"Size: {file_size} bytes\n"
                f"Status: completed"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[ToolExecutor] Download error: {e}")
            return f"Error: Download failed: {e}"
