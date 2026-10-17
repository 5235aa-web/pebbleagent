"""PDF reading and OCR."""

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..truncation_manager import truncate_output
from ..code_network_guardV2 import (
    scan_code_for_scraping_behavior,
    get_rate_limiter,
    get_path_depth_guard,
    get_download_guard,
    reset_all_guards,
)
from ..code_destructive_guard import scan_code_for_destructive_behavior

logger = logging.getLogger(__name__)



_easyocr_reader = None


_easyocr_languages = None


class DocumentToolsMixin:

    async def _tool_read_pdf(self, file_path: str, pages: Optional[str] = None, ocr_language: str = "ch_sim+en") -> str:
        """
        读取 PDF 文件
        """
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        if not os.path.exists(resolved_path):
            return f"Error: File not found: {resolved_path}"
        
        # ---------- PyMuPDF 主引擎 ----------
        try:
            import fitz
            doc = fitz.open(resolved_path)
        except ImportError:
            # ---------- 降级：pdfplumber（仅数字PDF，无OCR） ----------
            try:
                import pdfplumber
                with pdfplumber.open(resolved_path) as pdf:
                    total = len(pdf.pages)
                    target_indices = self._parse_page_range(pages if pages else None, total)
                    result = []
                    for i in target_indices:
                        page = pdf.pages[i - 1]
                        text = page.extract_text() or ""
                        result.append(f"--- 第 {i} 页 ---\n\n{text}")
                    return "\n\n".join(result) if result else "(PDF 内容为空)"
            except ImportError:
                return "Error: PDF 解析库未安装（需要 PyMuPDF 或 pdfplumber）"
            except Exception as e:
                return f"Error reading PDF with pdfplumber: {e}"
        
        # ---------- PyMuPDF 正常处理 ----------
        doc_ref = doc  # 给 finally 用
        try:
            total_pages = len(doc_ref)
            target_indices = self._parse_page_range(pages if pages else None, total_pages)
            result_pages = []
            
            for page_num in target_indices:
                page = doc_ref[page_num - 1]  # PyMuPDF 0-indexed
                
                # 1) 尝试直接提取文字
                text = page.get_text().strip()
                
                if text:
                    result_pages.append(f"--- 第 {page_num} 页 ---\n\n{text}")
                else:
                    ocr_text = await self._ocr_pdf_page(page, page_num, ocr_language)
                    result_pages.append(f"--- 第 {page_num} 页（OCR识别）---\n\n{ocr_text}")
            
            return "\n\n".join(result_pages) if result_pages else "(PDF 内容为空)"
        
        except Exception as e:
            return f"Error reading PDF: {e}"
        finally:
            doc_ref.close()


    def _parse_page_range(self, pages: Optional[str], total: int) -> List[int]:
        if not pages or pages.strip() == "":
            return list(range(1, total + 1))
        
        pages = pages.strip()
        selected = set()
        
        try:
            for part in pages.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-", 1)
                    start, end = int(start.strip()), int(end.strip())
                    selected.update(range(max(1, start), min(total, end) + 1))
                else:
                    num = int(part)
                    if 1 <= num <= total:
                        selected.add(num)
        except ValueError:
            # 解析失败时返回全部页面
            return list(range(1, total + 1))
        
        return sorted(selected) if selected else list(range(1, total + 1))


    async def _ocr_pdf_page(self, page, page_num: int, ocr_language: str) -> str:
        """对 PDF 单页执行 EasyOCR（在子线程中运行）"""
        try:
            import numpy as np
            from PIL import Image
            
            # 渲染页面为图片（300 DPI）
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            img_array = np.array(img)
            
            # 延迟初始化 EasyOCR（首次调用时加载模型）
            reader = await asyncio.to_thread(self._get_easyocr_reader, ocr_language)
            
            # OCR 识别（CPU 密集型，放入线程池）
            raw_results = await asyncio.to_thread(reader.readtext, img_array)
            
            if not raw_results:
                return "(无法识别该页内容)"
            
            # 拼接识别结果（保留相对位置）
            lines = []
            for item in raw_results:
                text = item[1].strip()
                if text:
                    lines.append(text)
            return "\n".join(lines)
        
        except ImportError as e:
            return f"(OCR 引擎不可用: {e})"
        except Exception as e:
            return f"(OCR 识别失败: {e})"


    def _get_easyocr_reader(self, languages: str = "ch_sim+en"):
        """全局 EasyOCR Reader 延迟初始化"""
        global _easyocr_reader, _easyocr_languages
        
        lang_list = [l.strip() for l in languages.replace("+", ",").split(",") if l.strip()]
        
        # 如果已缓存相同语言的 reader，直接返回
        if _easyocr_reader is not None and _easyocr_languages == lang_list:
            return _easyocr_reader
        
        import easyocr
        _easyocr_reader = easyocr.Reader(lang_list, gpu=True)
        _easyocr_languages = lang_list
        return _easyocr_reader
