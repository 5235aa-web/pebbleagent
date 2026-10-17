"""Filesystem, search and repository tools."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import List


from ..truncation_manager import truncate_output

logger = logging.getLogger(__name__)



def _stream_search(filepath: str, regex, pattern: str, deadline: float) -> tuple:
    """
    search_files 流式分块搜索：恒定内存（~块大小），支持任意大小文件。

    返回 (matched, is_binary, timed_out)：
    - 二进制检测：任意块含 NUL 字节即终止（与 ripgrep 语义对齐）
    - 跨块匹配：保留尾部 overlap（pattern 长度-1）与下一块拼接后搜索
    - 编码：增量 UTF-8 解码器，正确处理跨块切分的多字节字符
    - 时间：每块检查 deadline，超时返回 (False, False, True)
    """
    import codecs
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    overlap = max(0, len(pattern) - 1)
    tail = ""
    try:
        with open(filepath, "rb") as f:
            while True:
                if time.monotonic() > deadline:
                    return False, False, True
                raw = f.read(_SEARCH_CHUNK_BYTES)
                if not raw:
                    break
                if 0 in raw:  # NUL 字节检测：判定为二进制文件
                    return False, True, False
                window = tail + decoder.decode(raw)
                if regex.search(window):
                    return True, False, False
                if overlap:
                    tail = window[-overlap:]
    except OSError:
        return False, False, False
    return False, False, False


_SEARCH_SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv",
    "dist", "build", "target", ".next", ".nuxt", ".output", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "htmlcov",
}


_SEARCH_SKIP_EXTS = {
    # 可执行 / 库 / 编译产物
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pkg", ".pyz", ".pyc", ".pyo",
    ".rlib", ".rmeta", ".lib", ".obj", ".o", ".a", ".class", ".jar",
    # 归档 / 打包 / 安装介质
    ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".tar", ".whl", ".msi", ".iso", ".nupkg",
    # 数据库 / 二进制数据
    ".db", ".sqlite", ".sqlite3", ".mdb", ".pdb", ".dmp",
    # 媒体
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz",
    ".mp3", ".mp4", ".wav", ".flac", ".avi", ".mkv", ".mov", ".webm",
    # 字体
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # 文档（压缩容器格式，文本搜索无意义）
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
}


_SEARCH_CHUNK_BYTES = 1024 * 1024    # 流式读取块大小（1MB）


_SEARCH_MAX_RESULTS = 200            # 结果上限（达到即早退）


_SEARCH_TIME_BUDGET = 30.0           # 内部时间预算（秒，每块检查）


_SEARCH_OUTER_TIMEOUT = 35.0         # 外部兜底超时（秒，防单次 IO 卡死）


class FileToolsMixin:

    async def _tool_read_file(self, file_path: str, start_line: int = None, end_line: int = None) -> str:
        """读取文件（包含敏感文件检查）"""
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        
        # 敏感文件检查（记录审计日志）
        is_sensitive, sensitive_reason = self._enforcer.check_sensitive_file(resolved_path)
        if is_sensitive:
            audit_logger = self._enforcer._get_audit_logger()
            if audit_logger:
                from gateway.permission_audit import AuditEvent, AuditEventType, AuditDecisionSource
                audit_logger.log_event(
                    AuditEvent(
                        event_type=AuditEventType.PERMISSION_CHECK.value,
                        tool_name='read_file',
                        target=resolved_path,
                        allowed=False,
                        decision_source=AuditDecisionSource.PATH_VIOLATION.value,
                        reason=sensitive_reason
                    )
                )
            logger.warning(f"[ToolExecutor] Sensitive file access blocked: {resolved_path} - {sensitive_reason}")
            # 注意：敏感文件检查只是警告，不阻止读取（权限管理器已经检查过）
            # 但我们记录审计日志
        
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                if start_line and end_line:
                    lines = f.readlines()
                    content = ''.join(lines[start_line-1:end_line])
                elif start_line:
                    lines = f.readlines()
                    content = ''.join(lines[start_line-1:])
                else:
                    content = f.read()
            truncated, _ = truncate_output(content)
            return truncated
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_write_file(self, file_path: str, content: str, mode: str = "write") -> str:
        """写入文件（包含敏感文件检查）"""
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        logger.debug(f"[ToolExecutor] write_file: input_path={file_path}, resolved_path={resolved_path}, project_path={self._current_project_path}")
        
        # 敏感文件检查
        is_sensitive, sensitive_reason = self._enforcer.check_sensitive_file(resolved_path)
        if is_sensitive:
            audit_logger = self._enforcer._get_audit_logger()
            if audit_logger:
                from gateway.permission_audit import AuditEvent, AuditEventType
                audit_logger.log_event(
                    AuditEvent(
                        event_type=AuditEventType.PERMISSION_CHECK.value,
                        tool_name='write_file',
                        target=resolved_path,
                        allowed=True,  # 权限已通过，但记录敏感操作
                        decision_source='sensitive_file_warning',
                        reason=sensitive_reason
                    )
                )
            logger.warning(f"[ToolExecutor] Writing to sensitive file: {resolved_path} - {sensitive_reason}")
        
        try:
            # 确保目录存在
            dir_path = os.path.dirname(resolved_path)
            logger.debug(f"[ToolExecutor] write_file: creating directory={dir_path}")
            os.makedirs(dir_path, exist_ok=True)
            
            write_mode = 'a' if mode == "append" else 'w'
            logger.debug(f"[ToolExecutor] write_file: opening file with mode={write_mode}")
            with open(resolved_path, write_mode, encoding='utf-8') as f:
                f.write(content)
            
            # 验证文件是否真的写入成功
            if os.path.exists(resolved_path):
                file_size = os.path.getsize(resolved_path)
                logger.debug(f"[ToolExecutor] write_file: SUCCESS - file exists, size={file_size} bytes, path={resolved_path}")
            else:
                logger.debug(f"[ToolExecutor] write_file: FAILED - file does not exist after write! path={resolved_path}")
                return f"Error: File was not created at {resolved_path}"
            
            result = f"Success: Written to {resolved_path}"
            lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
            return result + lsp_report
        except Exception as e:
            logger.debug(f"[ToolExecutor] write_file: Exception - {str(e)}")
            return f"Error: {str(e)}"


    async def _tool_edit_file(self, file_path: str, operation: str = "", line_number: int = None,
                               content: str = None, end_line: int = None,
                               old_string: str = "", new_string: str = "", 
                               replace_all: bool = False,
                               search_block: str = "", replace_block: str = "") -> str:
        """
        编辑文件
        
        支持三种模式（优先级从高到低）：
        1.【最推荐】搜索-替换块（search_block + replace_block）
           - 带上下文的搜索块，模糊唯一匹配
           - 容忍缩进/空白差异，自动处理多候选歧义
           - 类似主流 AI 编程工具的编辑方式
        
        2.【推荐】字符串匹配替换（old_string + new_string）
           - 在文件中精确查找 old_string 并替换为 new_string
           - 不依赖行号，不受前面编辑操作影响
           - old_string 必须唯一匹配（除非 replace_all=true）
           - old_string 必须从 read_file 返回的内容中精确复制
        
        3.【备用】行号编辑（operation + line_number）
           - 按行号进行插入、删除、替换
           - 向后兼容原有行为
        
        优先级：search_block > old_string > operation
        """
        if not file_path:
            return "Error: No file path provided"
        
        resolved_path = self._resolve_path(file_path)
        logger.debug(f"[ToolExecutor] edit_file: input_path={file_path}, resolved_path={resolved_path}")
        
        # 检查文件是否存在
        if not os.path.exists(resolved_path):
            return f"Error: File not found: {resolved_path}"
        
        if not os.path.isfile(resolved_path):
            return f"Error: Path is not a file: {resolved_path}"
        
        # ================================================================
        # 模式 1：搜索-替换块（最推荐，模糊匹配 + 上下文防歧义）
        # ================================================================
        if search_block:
            return await self._tool_edit_file_by_search_replace(
                resolved_path=resolved_path,
                search_block=search_block,
                replace_block=replace_block
            )
        
        # ================================================================
        # 模式 2：字符串匹配替换（兼容旧版）
        # ================================================================
        if old_string:
            return await self._tool_edit_file_by_string(
                resolved_path=resolved_path,
                old_string=old_string,
                new_string=new_string,
                replace_all=replace_all
            )
        
        # ================================================================
        # 模式 3：行号编辑（回退，向后兼容）
        # ================================================================
        if not operation:
            return (
                "Error: 请提供 search_block（搜索-替换块，最推荐）、old_string（字符串匹配，推荐）或 operation（行号模式，备用）。\n\n"
                "搜索-替换块模式示例（最推荐）:\n"
                '  edit_file(file_path="x.py", search_block="def foo(x):\\n    return x + 1", replace_block="def foo(x):\\n    return x * 2")\n\n'
                "字符串匹配模式示例:\n"
                '  edit_file(file_path="x.py", old_string="def foo():\\n    pass", new_string="def foo():\\n    return 42")\n\n'
                "行号模式示例:\n"
                '  edit_file(file_path="x.py", operation="replace", line_number=10, content="new line")'
            )
        
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if operation == "insert":
                if line_number is None:
                    return "Error: line_number required for insert"
                if content is None:
                    return "Error: content required for insert"
                lines.insert(line_number - 1, content + '\n')
                description = f"在第 {line_number} 行插入内容"
            
            elif operation == "delete":
                if line_number is None:
                    return "Error: line_number required for delete"
                if end_line:
                    del lines[line_number - 1:end_line]
                    description = f"删除第 {line_number}-{end_line} 行"
                else:
                    deleted_content = lines[line_number - 1].rstrip('\n') if line_number - 1 < len(lines) else "(空)"
                    del lines[line_number - 1]
                    description = f"删除第 {line_number} 行（内容: {deleted_content[:80]}）"
            
            elif operation == "replace":
                if line_number is None:
                    return "Error: line_number required for replace"
                if content is None:
                    return "Error: content required for replace"
                if end_line:
                    lines[line_number - 1:end_line] = [content + '\n']
                    description = f"替换第 {line_number}-{end_line} 行"
                else:
                    lines[line_number - 1] = content + '\n'
            else:
                return f"Error: Unknown operation '{operation}'"
            
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            rel_path = os.path.relpath(resolved_path, self._current_project_path) if os.path.isabs(resolved_path) else resolved_path
            result = f"Success: {description} — {rel_path}"
            lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
            return result + lsp_report
        
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except PermissionError:
            return f"Error: Permission denied: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_edit_file_by_string(
        self, resolved_path: str, old_string: str, new_string: str, 
        replace_all: bool = False
    ) -> str:
        """
        字符串匹配替换模式（推荐）
        
        字符串匹配替换的核心逻辑：
        1. 读取文件内容
        2. 精确查找 old_string
        3. 验证唯一性（除非 replace_all）
        4. 执行替换
        5. 返回结构化结果（含 diff 摘要）
        
        Args:
            resolved_path: 已解析的绝对文件路径
            old_string: 要替换的旧文本（精确匹配，含缩进和换行）
            new_string: 替换后的新文本
            replace_all: 是否替换所有匹配项
        
        Returns:
            执行结果字符串
        """
        # 读取文件
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except PermissionError:
            return f"Error: Permission denied: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to read file: {str(e)}"
        
        # 验证 old_string 非空
        if not old_string:
            return "Error: old_string 不能为空。请提供要替换的精确文本片段。"
        
        # 统计匹配次数
        match_count = file_content.count(old_string)
        
        # 未找到匹配
        if match_count == 0:
            # 提供帮助信息：显示文件的前 500 字符
            preview = file_content[:500]
            if len(file_content) > 500:
                preview += "\n... (文件共 {} 行，{} 字符，仅显示前 500 字符)".format(
                    file_content.count('\n') + 1, len(file_content)
                )
            
            # 尝试找到最接近的匹配行（帮助 LLM 定位错误）
            old_first_line = old_string.split('\n')[0].strip() if old_string else ""
            hint = ""
            if old_first_line:
                file_lines = file_content.split('\n')
                for i, line in enumerate(file_lines, 1):
                    if old_first_line[:20] in line:
                        hint = (
                            f"\n\n提示：文件中第 {i} 行包含相似内容，但缩进或空格可能不匹配。"
                            f"\n文件中的实际内容: {repr(line[:100])}"
                            f"\n你提供的 old_string: {repr(old_string[:100])}"
                            f"\n请从 read_file 返回的行号前缀后的内容中精确复制（包括所有缩进和空格）。"
                        )
                        break
            
            return (
                f"Error: 在文件中未找到 old_string（匹配次数: 0）。\n\n"
                f"文件路径: {resolved_path}\n"
                f"文件大小: {len(file_content)} 字符, {file_content.count(chr(10)) + 1} 行\n"
                f"搜索的文本（前 200 字符）: {repr(old_string[:200])}\n\n"
                f"文件内容预览:\n```\n{preview}\n```"
                f"{hint}"
            )
        
        # 多处匹配且未指定 replace_all
        if match_count > 1 and not replace_all:
            # 找出每个匹配位置的行号
            positions = []
            search_start = 0
            while True:
                pos = file_content.find(old_string, search_start)
                if pos == -1:
                    break
                line_num = file_content[:pos].count('\n') + 1
                # 显示匹配位置的上下文
                context_start = max(0, pos - 30)
                context_end = min(len(file_content), pos + len(old_string) + 30)
                context = file_content[context_start:context_end].replace('\n', '\\n')
                positions.append(f"  行 {line_num}: ...{repr(context[:80])}...")
                search_start = pos + 1
            
            positions_text = '\n'.join(positions[:10])
            if len(positions) > 10:
                positions_text += f"\n  ... 还有 {len(positions) - 10} 处匹配"
            
            return (
                f"Error: old_string 在文件中出现了 {match_count} 次，而不是唯一一次。\n\n"
                f"请选择以下方案之一:\n"
                f"1. 提供更多上下文行使 old_string 变得唯一（推荐）——在 old_string 前后各加 1-2 行\n"
                f"2. 设置 replace_all=true 替换所有 {match_count} 处匹配（仅适用于全局重命名）\n\n"
                f"匹配位置:\n{positions_text}"
            )
        
        # 计算 old_string 在文件中的起始行号
        first_pos = file_content.find(old_string)
        start_line = file_content[:first_pos].count('\n') + 1 if first_pos >= 0 else 1

        # 执行替换
        if replace_all:
            new_file_content = file_content.replace(old_string, new_string)
            replaced_count = match_count
        else:
            new_file_content = file_content.replace(old_string, new_string, 1)
            replaced_count = 1
        
        # 写入文件
        try:
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_file_content)
        except PermissionError:
            return f"Error: Permission denied when writing: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to write file: {str(e)}"
        
        # 构建返回信息
        try:
            rel_path = (
                os.path.relpath(resolved_path, self._current_project_path) 
                if os.path.isabs(resolved_path) else resolved_path
            )
        except ValueError:
            rel_path = os.path.basename(resolved_path)
        
        # 生成 diff 摘要
        old_preview = old_string[:80].replace('\n', '\\n')
        new_preview = new_string[:80].replace('\n', '\\n')
        if len(old_string) > 80:
            old_preview += "..."
        if len(new_string) > 80:
            new_preview += "..."
        
        if replaced_count == 1:
            result = (
                f"Success: 文件已编辑 — {rel_path}\n"
                f"  Line: {start_line}\n"
                f"  旧: {old_preview}\n"
                f"  新: {new_preview}"
            )
        else:
            result = (
                f"Success: 文件已编辑 — {rel_path}\n"
                f"  Line: {start_line}（首处）\n"
                f"  替换了 {replaced_count} 处匹配（replace_all=true）\n"
                f"  旧: {old_preview}\n"
                f"  新: {new_preview}"
            )
        lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
        return result + lsp_report


    def _fuzzy_find_all(self, content: str, search_block: str, threshold: float = 0.85) -> list:
        """
        滑动窗口模糊匹配，返回所有候选位置及分数
        
        Args:
            content: 文件完整内容
            search_block: 搜索块文本
            threshold: 相似度阈值
        
        Returns:
            候选匹配列表，每项包含 start_line, end_line, score, matched_lines
        """
        import difflib
        
        def normalize_line(line: str) -> str:
            """归一化行：去除首尾空白，折叠连续空白"""
            return ' '.join(line.strip().split())
        
        search_lines = [normalize_line(l) for l in search_block.splitlines() if normalize_line(l)]
        if not search_lines:
            return []
        
        content_lines = content.splitlines()
        content_normalized = [normalize_line(l) for l in content_lines]
        window = len(search_lines)
        
        if window > len(content_lines):
            return []
        
        candidates = []
        for i in range(len(content_lines) - window + 1):
            window_lines = content_normalized[i:i+window]
            
            # 快速检查：首行和尾行必须高度匹配（加权）
            first_match = difflib.SequenceMatcher(None, search_lines[0], window_lines[0]).ratio()
            last_match = difflib.SequenceMatcher(None, search_lines[-1], window_lines[-1]).ratio()
            
            if first_match < 0.6 or last_match < 0.6:
                continue
            
            # 计算整体相似度
            matcher = difflib.SequenceMatcher(None, search_lines, window_lines)
            score = matcher.ratio()
            
            if score >= threshold:
                candidates.append({
                    'start_line': i + 1,
                    'end_line': i + window,
                    'score': score,
                    'matched_content': '\n'.join(content_lines[i:i+window])
                })
        
        # 按分数降序排序
        candidates.sort(key=lambda x: -x['score'])
        return candidates


    def _apply_search_replace(self, content: str, search_block: str, replace_block: str) -> tuple[str, str]:
        """
        应用搜索-替换块，返回 (新内容, 错误信息)
        错误信息为空表示成功
        """
        # 1. 精确匹配优先（兼容现有行为）
        if search_block in content:
            count = content.count(search_block)
            if count == 1:
                new_content = content.replace(search_block, replace_block)
                return new_content, ""
            elif count > 1:
                return content, f"Error: search_block 在文件中出现了 {count} 次，不是唯一匹配。请提供更多上下文使其唯一，或使用 replace_all=true。"
        
        # 2. 模糊匹配
        candidates = self._fuzzy_find_all(content, search_block)
        
        if not candidates:
            # 生成提示：显示文件前几行帮助定位
            preview_lines = content.splitlines()[:20]
            preview = '\n'.join(f"{i+1:4}: {line}" for i, line in enumerate(preview_lines))
            if len(content.splitlines()) > 20:
                preview += f"\n... (共 {len(content.splitlines())} 行)"
            
            return content, (
                f"Error: 未找到匹配的 search_block（模糊匹配阈值 0.85）。\n\n"
                f"文件内容预览（前 20 行）:\n```\n{preview}\n```\n\n"
                f"请确保 search_block 包含足够的上下文（建议 ≥ 3 行），并从 read_file 返回的内容中复制。"
            )
        
        if len(candidates) == 1:
            # 唯一高分匹配，直接替换
            c = candidates[0]
            content_lines = content.splitlines()
            new_lines = content_lines[:c['start_line']-1] + replace_block.splitlines() + content_lines[c['end_line']:]
            return '\n'.join(new_lines), ""
        
        # 3. 多候选：返回前 3 个让 LLM 选择
        top3 = candidates[:3]
        candidates_text = '\n'.join(
            f"  候选 {i+1} (行 {c['start_line']}-{c['end_line']}, 分数 {c['score']:.2f}):\n```\n{c['matched_content'][:200]}\n```"
            for i, c in enumerate(top3)
        )
        return content, (
            f"Error: 找到 {len(candidates)} 处相似匹配，无法自动确定唯一位置。\n\n"
            f"请在 search_block 中补充更多上下文行以消除歧义。\n\n"
            f"前 3 个候选位置:\n{candidates_text}"
        )


    async def _tool_edit_file_by_search_replace(
        self, resolved_path: str, search_block: str, replace_block: str
    ) -> str:
        """
        搜索-替换块编辑模式（推荐）
        
        使用带上下文的搜索块进行模糊唯一匹配，比 old_string/new_string 更健壮。
        
        Args:
            resolved_path: 已解析的绝对文件路径
            search_block: 搜索块（包含上下文的旧代码片段）
            replace_block: 替换块（新代码片段，保持相同缩进结构）
        
        Returns:
            执行结果字符串
        """
        # 读取文件
        try:
            with open(resolved_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except PermissionError:
            return f"Error: Permission denied: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to read file: {str(e)}"
        
        if not search_block:
            return "Error: search_block 不能为空。请提供要替换的代码片段（含上下文）。"
        
        # 应用搜索-替换
        new_content, error = self._apply_search_replace(file_content, search_block, replace_block)
        
        if error:
            return error
        
        # 写入文件
        try:
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except PermissionError:
            return f"Error: Permission denied when writing: {resolved_path}"
        except Exception as e:
            return f"Error: Failed to write file: {str(e)}"
        
        # 计算变更行号（用于显示）
        import difflib
        diff = list(difflib.unified_diff(
            file_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            n=3
        ))
        
        # 构建返回信息
        try:
            rel_path = (
                os.path.relpath(resolved_path, self._current_project_path) 
                if os.path.isabs(resolved_path) else resolved_path
            )
        except ValueError:
            # 不同驱动器下 relpath 会失败，回退到文件名
            rel_path = os.path.basename(resolved_path)
        
        # 尝试找到变更的大致行号
        start_line = 1
        for line in diff:
            if line.startswith('@@'):
                import re
                m = re.search(r'@@ -(\d+)', line)
                if m:
                    start_line = int(m.group(1))
                    break
        
        old_preview = search_block[:80].replace('\n', '\\n')
        new_preview = replace_block[:80].replace('\n', '\\n')
        if len(search_block) > 80:
            old_preview += "..."
        if len(replace_block) > 80:
            new_preview += "..."
        
        result = (
            f"Success: 文件已编辑 (search/replace) — {rel_path}\n"
            f"  Line: ~{start_line}\n"
            f"  旧: {old_preview}\n"
            f"  新: {new_preview}"
        )
        lsp_report = await self._lsp_diagnostics_after_write(resolved_path)
        return result + lsp_report


    async def _tool_delete_file(self, path: str, recursive: bool = False) -> str:
        """删除文件或目录"""
        if not path:
            return "Error: No path provided"
        
        
        resolved_path = self._resolve_path(path)
        
        try:
            if os.path.isfile(resolved_path):
                os.remove(resolved_path)
                return f"Success: File deleted: {resolved_path}"
            elif os.path.isdir(resolved_path):
                if recursive:
                    shutil.rmtree(resolved_path)
                    return f"Success: Directory deleted recursively: {resolved_path}"
                else:
                    os.rmdir(resolved_path)
                    return f"Success: Directory deleted: {resolved_path}"
            else:
                return f"Error: Path not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_copy_file(self, source: str, destination: str) -> str:
        """复制文件或目录"""
        if not source or not destination:
            return "Error: Source and destination paths required"
        
        
        resolved_source = self._resolve_path(source)
        resolved_destination = self._resolve_path(destination)
        
        try:
            if os.path.isfile(resolved_source):
                shutil.copy2(resolved_source, resolved_destination)
                return f"Success: File copied from {resolved_source} to {resolved_destination}"
            elif os.path.isdir(resolved_source):
                shutil.copytree(resolved_source, resolved_destination)
                return f"Success: Directory copied from {resolved_source} to {resolved_destination}"
            else:
                return f"Error: Source not found: {resolved_source}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_move_file(self, source: str, destination: str) -> str:
        """移动/重命名文件或目录"""
        if not source or not destination:
            return "Error: Source and destination paths required"
        
        
        resolved_source = self._resolve_path(source)
        resolved_destination = self._resolve_path(destination)
        
        try:
            shutil.move(resolved_source, resolved_destination)
            return f"Success: Moved from {resolved_source} to {resolved_destination}"
        except FileNotFoundError:
            return f"Error: Source not found: {resolved_source}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_list_directory(self, path: str, show_hidden: bool = False, recursive: bool = False) -> str:
        """列出目录内容"""
        if not path:
            return "Error: No path provided"
        
        resolved_path = self._resolve_path(path)
        
        try:
            if recursive:
                result = []
                for root, dirs, files in os.walk(resolved_path):
                    for d in dirs:
                        if not show_hidden and d.startswith('.'):
                            continue
                        result.append(f"[DIR]  {os.path.join(root, d)}")
                    for f in files:
                        if not show_hidden and f.startswith('.'):
                            continue
                        full_path = os.path.join(root, f)
                        size = os.path.getsize(full_path)
                        result.append(f"[FILE] {full_path} ({size} bytes)")
            else:
                entries = os.listdir(resolved_path)
                result = []
                for entry in entries:
                    if not show_hidden and entry.startswith('.'):
                        continue
                    full_path = os.path.join(resolved_path, entry)
                    if os.path.isdir(full_path):
                        result.append(f"[DIR]  {entry}")
                    else:
                        size = os.path.getsize(full_path)
                        result.append(f"[FILE] {entry} ({size} bytes)")
            return "\n".join(result)
        except FileNotFoundError:
            return f"Error: Directory not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_create_directory(self, path: str) -> str:
        """创建目录"""
        if not path:
            return "Error: No path provided"
        
        resolved_path = self._resolve_path(path)
        
        try:
            os.makedirs(resolved_path, exist_ok=True)
            return f"Success: Directory created: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_get_file_info(self, path: str) -> str:
        """获取文件信息"""
        if not path:
            return "Error: No path provided"
        
        resolved_path = self._resolve_path(path)
        
        try:
            stat = os.stat(resolved_path)
            return json.dumps({
                "path": resolved_path,
                "size": stat.st_size,
                "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime)),
                "created": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_ctime)),
                "is_directory": os.path.isdir(resolved_path)
            }, indent=2)
        except FileNotFoundError:
            return f"Error: Path not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_grep(self, pattern: str, path: str = None, glob_pattern: str = None,
                          output_mode: str = "files_with_matches", case_insensitive: bool = False,
                          show_line_numbers: bool = True, after_context: int = None,
                          before_context: int = None, context: int = None, file_type: str = None,
                          head_limit: int = 250) -> str:
        """使用 ripgrep 进行文件内容搜索"""
        if not pattern:
            return "Error: No pattern provided"
        
        search_path = self._resolve_path(path) if path else self._current_project_path
        
        cmd = ["rg"]
        
        if output_mode == "content":
            if show_line_numbers:
                cmd.append("-n")
            if after_context:
                cmd.extend(["-A", str(after_context)])
            if before_context:
                cmd.extend(["-B", str(before_context)])
            if context:
                cmd.extend(["-C", str(context)])
        elif output_mode == "count":
            cmd.append("-c")
        else:
            cmd.append("-l")
        
        if case_insensitive:
            cmd.append("-i")
        
        for vcs_dir in ['.git', '.svn', '.hg']:
            cmd.extend(["--glob", f"!{vcs_dir}"])
        
        if glob_pattern:
            cmd.extend(["--glob", glob_pattern])
        
        if file_type:
            cmd.extend(["--type", file_type])
        
        cmd.append("--")
        cmd.append(pattern)
        cmd.append(search_path)
        
        try:
            # rg 输出恒为 UTF-8；显式 encoding 防止中文 Windows 上 GBK 解码失败导致
            # stdout=None 被静默丢弃（表现为"文件明明有却返回 No matches found"）
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                    encoding='utf-8', errors='replace',
                                    stdin=subprocess.DEVNULL)
            
            if result.returncode not in [0, 1]:
                return f"Error: {result.stderr}" if result.stderr else "Search failed"
            
            output = (result.stdout or '').strip()
            
            if not output:
                return "No matches found"
            
            if output_mode == "files_with_matches":
                lines = output.split('\n')
                all_files = [line for line in lines if line.strip()]
                sliced_files = all_files[:head_limit] if head_limit > 0 else all_files
                return json.dumps({
                    "mode": output_mode,
                    "numFiles": len(sliced_files),
                    "filenames": sliced_files
                }, ensure_ascii=False, indent=2)
            
            elif output_mode == "count":
                lines = output.split('\n')
                counts = []
                for line in lines:
                    if ':' in line:
                        filepath, count = line.rsplit(':', 1)
                        counts.append({"file": filepath, "count": int(count.strip())})
                return json.dumps({
                    "mode": output_mode,
                    "numFiles": len(counts),
                    "counts": counts[:head_limit] if head_limit > 0 else counts
                }, ensure_ascii=False, indent=2)
            
            else:
                lines = output.split('\n')
                sliced_lines = lines[:head_limit] if head_limit > 0 else lines
                return json.dumps({
                    "mode": output_mode,
                    "numLines": len(sliced_lines),
                    "content": '\n'.join(sliced_lines)
                }, ensure_ascii=False, indent=2)
        
        except subprocess.TimeoutExpired:
            return "Error: Search timed out"
        except FileNotFoundError:
            return "Error: ripgrep (rg) not found"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_glob(self, pattern: str, path: str = None) -> str:
        """使用 glob 模式匹配查找文件"""
        if not pattern:
            return "Error: No pattern provided"
        
        import fnmatch
        
        search_path = self._resolve_path(path) if path else self._current_project_path
        
        if not os.path.exists(search_path):
            return f"Error: Directory does not exist: {search_path}"
        
        matches = []
        
        try:
            for root, dirs, files in os.walk(search_path):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in 
                          ['node_modules', '__pycache__', '.git', 'venv', 'dist', 'build']]
                
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern.replace('**/', '').replace('/**', '')):
                        matches.append(os.path.join(root, filename))
            
            matches.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
            
            max_results = 100
            truncated = len(matches) > max_results
            if truncated:
                matches = matches[:max_results]
            
            return json.dumps({
                "numFiles": len(matches),
                "filenames": matches,
                "truncated": truncated
            }, ensure_ascii=False, indent=2)
        
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_ls(self, path: str, ignore: List[str] = None) -> str:
        """列出目录内容"""
        if not path:
            return "Error: No path provided"
        
        import fnmatch
        
        resolved_path = self._resolve_path(path)
        
        if not os.path.exists(resolved_path):
            return f"Error: Path does not exist: {resolved_path}"
        
        if not os.path.isdir(resolved_path):
            return f"Error: Path is not a directory: {resolved_path}"
        
        ignore_patterns = ignore or []
        
        try:
            entries = []
            
            for entry in os.listdir(resolved_path):
                should_ignore = False
                for pattern in ignore_patterns:
                    if fnmatch.fnmatch(entry, pattern):
                        should_ignore = True
                        break
                
                if not should_ignore:
                    full_path = os.path.join(resolved_path, entry)
                    is_dir = os.path.isdir(full_path)
                    entries.append({
                        "name": entry,
                        "type": "directory" if is_dir else "file",
                        "path": full_path
                    })
            
            entries.sort(key=lambda x: (x["type"] == "file", x["name"].lower()))
            
            return json.dumps({
                "path": resolved_path,
                "entries": entries,
                "count": len(entries)
            }, ensure_ascii=False, indent=2)
        
        except PermissionError:
            return "Error: Permission denied"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_search_in_file(self, file_path: str, pattern: str, ignore_case: bool = True) -> str:
        """在文件中搜索内容"""
        if not file_path or not pattern:
            return "Error: file_path and pattern required"
        
        
        resolved_path = self._resolve_path(file_path)
        
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
            
            with open(resolved_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            results = []
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    results.append(f"Line {i}: {line.rstrip()}")
            
            if results:
                return f"Found {len(results)} matches:\n" + "\n".join(results[:50])
            else:
                return "No matches found"
        
        except FileNotFoundError:
            return f"Error: File not found: {resolved_path}"
        except Exception as e:
            return f"Error: {str(e)}"


    async def _tool_search_files(self, directory: str, pattern: str, search_type: str = "name",
                                  file_pattern: str = None,
                                  _max_seconds: float = _SEARCH_TIME_BUDGET,
                                  _max_results: int = _SEARCH_MAX_RESULTS) -> str:
        """
        在目录中搜索文件名或内容（加固版）。

        加固点：
        - asyncio.to_thread 执行，不阻塞事件循环（外部 wait_for 兜底防单次 IO 卡死）
        - 目录剪枝（隐藏目录 + 依赖/构建/缓存目录），显式指定起点目录不受剪枝影响
        - content/both 模式：流式分块搜索（内存恒定），扩展名黑名单 + NUL 内容检测过滤二进制
        - 时间预算（每块检查）与结果上限（达到即早退）
        """
        if not directory or not pattern:
            return "Error: directory and pattern required"

        resolved_dir = self._resolve_path(directory)

        if not os.path.isdir(resolved_dir):
            return f"Error: Directory not found: {resolved_dir}"

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._search_files_sync,
                    resolved_dir, pattern, search_type, file_pattern,
                    _max_seconds, _max_results,
                ),
                timeout=_SEARCH_OUTER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return (f"Error: search timed out after {_SEARCH_OUTER_TIMEOUT:.0f}s "
                    f"(directory: {resolved_dir}). Try a narrower directory or use grep.")
        except Exception as e:
            return f"Error: {str(e)}"


    def _search_files_sync(self, resolved_dir: str, pattern: str, search_type: str,
                           file_pattern: str, max_seconds: float, max_results: int) -> str:
        """search_files 同步实现（在线程池中运行，不阻塞事件循环）"""
        import fnmatch

        start_time = time.monotonic()
        deadline = start_time + max_seconds
        results: List[str] = []
        skipped_binary = 0
        limit_reached = False
        timed_out = False

        regex = None
        if search_type in ("content", "both"):
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        try:
            for root, dirs, files in os.walk(resolved_dir):
                # 目录剪枝：隐藏目录 + 依赖/构建/缓存目录（仅剪子目录，起点目录不受影响）
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d.lower() not in _SEARCH_SKIP_DIRS
                ]

                for filename in files:
                    # 时间预算检查（每文件检查点）
                    if time.monotonic() > deadline:
                        timed_out = True
                        break

                    # 文件过滤
                    if file_pattern and not fnmatch.fnmatch(filename, file_pattern):
                        continue

                    filepath = os.path.join(root, filename)
                    match = False

                    if search_type in ("name", "both"):
                        if pattern.lower() in filename.lower():
                            match = True

                    if search_type in ("content", "both") and not match:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in _SEARCH_SKIP_EXTS:
                            skipped_binary += 1
                        else:
                            is_match, is_binary, f_timeout = _stream_search(
                                filepath, regex, pattern, deadline
                            )
                            if f_timeout:
                                timed_out = True
                                break
                            if is_binary:
                                skipped_binary += 1
                            elif is_match:
                                match = True

                    if match:
                        results.append(filepath)
                        if len(results) >= max_results:
                            limit_reached = True
                            break

                if timed_out or limit_reached:
                    break

        except Exception as e:
            return f"Error: {str(e)}"

        notes = []
        if timed_out:
            notes.append(f"timed out after {max_seconds:.0f}s, partial results")
        if limit_reached:
            notes.append(f"result limit reached ({max_results}), search stopped early")
        if skipped_binary:
            notes.append(f"{skipped_binary} binary file(s) skipped")

        if results:
            output = f"Found {len(results)} files:\n" + "\n".join(results)
        else:
            output = "No files found"
        if notes:
            output += "\nNote: " + "; ".join(notes)
        return output


    async def _tool_find_files(self, directory: str, pattern: str, max_depth: int = None) -> str:
        """查找文件"""
        if not directory or not pattern:
            return "Error: directory and pattern required"
        
        import fnmatch
        
        resolved_dir = self._resolve_path(directory)
        
        try:
            results = []
            base_depth = resolved_dir.count(os.sep)
            
            for root, dirs, files in os.walk(resolved_dir):
                # 深度检查
                if max_depth is not None:
                    current_depth = root.count(os.sep) - base_depth
                    if current_depth > max_depth:
                        continue
                
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern):
                        results.append(os.path.join(root, filename))
            
            if results:
                return f"Found {len(results)} files:\n" + "\n".join(results[:50])
            else:
                return "No files found"
        
        except FileNotFoundError:
            return f"Error: Directory not found: {resolved_dir}"
        except Exception as e:
            return f"Error: {str(e)}"
