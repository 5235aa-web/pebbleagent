"""
Tool Definitions - 工具定义

提供完整的 OpenAI 格式工具定义
支持 MCP 工具动态合并
"""

from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def get_tools_definition() -> List[Dict[str, Any]]:
    """
    获取 OpenAI 格式的工具定义
    
    Returns:
        工具定义列表
    """
    return [
        # ==================== 代码执行 ====================
        {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "执行Python代码并返回结果。可以执行任意Python代码，支持多行代码块。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "要执行的Python代码"
                        },
                        "language": {
                            "type": "string",
                            "description": "编程语言",
                            "enum": ["python", "javascript", "bash"],
                            "default": "python"
                        }
                    },
                    "required": ["code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_shell",
                "description": "执行Shell/命令行命令并返回结果。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的Shell命令"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "超时时间（秒）",
                            "default": 60
                        }
                    },
                    "required": ["command"]
                }
            }
        },
        # ==================== 文件操作 ====================
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取文件内容。支持文本文件、代码文件、配置文件等。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径（绝对路径或相对路径）"
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "起始行号（可选）"
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "结束行号（可选）"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_pdf",
                "description": "读取PDF文件内容并提取文字。自动识别数字PDF（直接提取文字）和扫描版PDF（自动OCR识别）。支持中英文混合文档。返回Markdown格式文本。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "PDF文件路径（绝对路径或相对路径）"
                        },
                        "pages": {
                            "type": "string",
                            "description": "页码范围，如 '1-5'（第1到5页）、'1,3,5'（第1/3/5页）。留空表示全部页面。"
                        },
                        "ocr_language": {
                            "type": "string",
                            "description": "OCR识别语言，默认 'ch_sim+en'（中文简体+英文）。可选：'en'（英文）、'ch_sim'（中文简体）、'ch_sim+en'（中英混合）、'ja'（日文）等。对数字PDF无效。"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写入文件内容。如果文件不存在则创建。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的内容"
                        },
                        "mode": {
                            "type": "string",
                            "description": "写入模式",
                            "enum": ["write", "append"],
                            "default": "write"
                        }
                    },
                    "required": ["file_path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "编辑文件。支持三种模式（优先级从高到低）：\n1.【最推荐】搜索-替换块：提供 search_block 和 replace_block。search_block 包含要替换的代码片段及其上下文（前后各 1-3 行），引擎使用模糊匹配自动定位唯一位置。容忍缩进/空白差异，自动处理多候选歧义。类似 Claude Code/Aider/Cursor 的编辑方式，最健壮。\n2.【推荐】字符串匹配替换：提供 old_string 和 new_string，精确查找并替换文件中的文本片段。old_string 必须在文件中唯一出现（除非指定 replace_all=true）。\n3. 行号编辑：提供 operation 和 line_number，按行号进行插入/删除/替换。\n\n使用前必须先用 read_file 读取目标文件。search_block/old_string 必须从 read_file 返回的行号前缀后的实际文件内容中复制（保留精确的缩进和空格），不能包含行号前缀。\n\n优先创建新文件使用 write_file，本工具仅用于编辑已存在的文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径（绝对路径或相对于项目目录的路径）"
                        },
                        "search_block": {
                            "type": "string",
                            "description": "【搜索-替换块模式 — 最推荐】要替换的代码片段及其上下文（建议包含前后各 1-3 行）。引擎使用模糊匹配自动定位，容忍缩进/空白差异。必须提供足够上下文以确保唯一匹配。"
                        },
                        "replace_block": {
                            "type": "string",
                            "description": "【搜索-替换块模式 — 最推荐】替换后的新代码片段。保持与 search_block 相同的缩进结构。删除内容时传空字符串或仅保留上下文行。"
                        },
                        "old_string": {
                            "type": "string",
                            "description": "【字符串匹配模式 — 推荐】要替换的旧文本片段。必须从文件中精确复制（含缩进、空格、换行）。必须在文件中唯一出现，否则编辑会失败。如果多处出现，请提供更多上下文行使其唯一，或使用 replace_all=true。"
                        },
                        "new_string": {
                            "type": "string",
                            "description": "【字符串匹配模式 — 推荐】替换后的新文本片段。删除内容时传空字符串。"
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "是否替换文件中所有匹配 old_string 的位置。默认 false（要求唯一匹配）。设为 true 可用于重命名变量等批量替换。",
                            "default": False
                        },
                        "operation": {
                            "type": "string",
                            "description": "【行号模式 — 备用】操作类型。字符串匹配模式下不需要此参数。",
                            "enum": ["insert", "delete", "replace"]
                        },
                        "line_number": {
                            "type": "integer",
                            "description": "【行号模式 — 备用】行号（从1开始）。字符串匹配模式下不需要此参数。"
                        },
                        "content": {
                            "type": "string",
                            "description": "【行号模式 — 备用】插入/替换的内容。字符串匹配模式下使用 new_string。"
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "【行号模式 — 备用】结束行号（用于范围删除/替换）。"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "删除文件或目录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要删除的文件或目录路径"
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "是否递归删除目录",
                            "default": False
                        }
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "copy_file",
                "description": "复制文件或目录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "源路径"
                        },
                        "destination": {
                            "type": "string",
                            "description": "目标路径"
                        }
                    },
                    "required": ["source", "destination"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "move_file",
                "description": "移动/重命名文件或目录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "源路径"
                        },
                        "destination": {
                            "type": "string",
                            "description": "目标路径"
                        }
                    },
                    "required": ["source", "destination"]
                }
            }
        },
        # ==================== 目录操作 ====================
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "列出目录内容。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "目录路径"
                        },
                        "show_hidden": {
                            "type": "boolean",
                            "description": "是否显示隐藏文件",
                            "default": False
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "是否递归列出子目录",
                            "default": False
                        }
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "create_directory",
                "description": "创建目录（支持多级创建）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "目录路径"
                        }
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_info",
                "description": "获取文件或目录的详细信息。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件或目录路径"
                        }
                    },
                    "required": ["path"]
                }
            }
        },
        # ==================== 搜索 ====================
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "使用 ripgrep 进行文件内容搜索。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "搜索模式（正则表达式）"
                        },
                        "path": {
                            "type": "string",
                            "description": "搜索路径"
                        },
                        "glob": {
                            "type": "string",
                            "description": "文件过滤模式"
                        },
                        "output_mode": {
                            "type": "string",
                            "description": "输出模式",
                            "enum": ["files_with_matches", "content", "count"],
                            "default": "files_with_matches"
                        },
                        "-i": {
                            "type": "boolean",
                            "description": "忽略大小写",
                            "default": False
                        },
                        "-n": {
                            "type": "boolean",
                            "description": "显示行号",
                            "default": True
                        },
                        "-A": {
                            "type": "integer",
                            "description": "显示匹配后的行数"
                        },
                        "-B": {
                            "type": "integer",
                            "description": "显示匹配前的行数"
                        },
                        "-C": {
                            "type": "integer",
                            "description": "显示匹配前后的行数"
                        },
                        "type": {
                            "type": "string",
                            "description": "文件类型"
                        },
                        "head_limit": {
                            "type": "integer",
                            "description": "结果数量限制",
                            "default": 250
                        }
                    },
                    "required": ["pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "glob",
                "description": "使用 glob 模式匹配查找文件。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "glob 模式（如 **/*.py）"
                        },
                        "path": {
                            "type": "string",
                            "description": "搜索路径"
                        }
                    },
                    "required": ["pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "ls",
                "description": "列出目录内容。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "目录路径"
                        },
                        "ignore": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "忽略的模式列表"
                        }
                    },
                    "required": ["path"]
                }
            }
        },
        # ==================== Git 操作 ====================
        {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": "获取Git状态。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "仓库路径",
                            "default": "."
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "git_log",
                "description": "获取Git日志。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "仓库路径",
                            "default": "."
                        },
                        "count": {
                            "type": "integer",
                            "description": "提交数量",
                            "default": 10
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "git_diff",
                "description": "获取Git差异。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "仓库路径",
                            "default": "."
                        },
                        "target": {
                            "type": "string",
                            "description": "比较目标"
                        }
                    },
                    "required": []
                }
            }
        },
        # ==================== 网络操作 ====================
        {
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": "获取网页内容。注意：SPA站点（JS渲染）可能回退到浏览器获取，请勿用于批量爬取。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL地址"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "超时时间（秒）",
                            "default": 30
                        }
                    },
                    "required": ["url"]
                }
            }
        },
        # ==================== 系统信息 ====================
        {
            "type": "function",
            "function": {
                "name": "get_env_var",
                "description": "获取环境变量。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "变量名"
                        }
                    },
                    "required": ["name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_system_info",
                "description": "获取系统信息。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_processes",
                "description": "列出进程。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "进程名过滤"
                        }
                    },
                    "required": []
                }
            }
        },
        # ==================== Todo 管理 ====================
        {
            "type": "function",
            "function": {
                "name": "todo_write",
                "description": "更新任务列表。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": ["pending", "in_progress", "completed"]
                                    },
                                    "priority": {
                                        "type": "string",
                                        "enum": ["high", "medium", "low"]
                                    }
                                }
                            },
                            "description": "任务列表"
                        }
                    },
                    "required": ["todos"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "todo_read",
                "description": "读取当前任务列表。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        # ==================== 用户交互 ====================
        {
            "type": "function",
            "function": {
                "name": "ask_user_question",
                "description": "向用户提出多选题问题。需要等待用户响应，必须串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string"},
                                    "header": {"type": "string"},
                                    "options": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {"type": "string"},
                                                "description": {"type": "string"}
                                            }
                                        }
                                    }
                                }
                            },
                            "description": "问题列表（1-4个）"
                        }
                    },
                    "required": ["questions"]
                }
            }
        },
        # ==================== PowerShell ====================
        {
            "type": "function",
            "function": {
                "name": "powershell",
                "description": "执行 PowerShell 命令。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "PowerShell 命令"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "超时时间（毫秒）",
                            "default": 60000
                        },
                        "run_in_background": {
                            "type": "boolean",
                            "description": "是否后台运行",
                            "default": False
                        }
                    },
                    "required": ["command"]
                }
            }
        },
        # ==================== 文件搜索 ====================
        {
            "type": "function",
            "function": {
                "name": "search_in_file",
                "description": "在文件中搜索文本内容，支持正则表达式。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "pattern": {
                            "type": "string",
                            "description": "搜索模式（支持正则表达式）"
                        },
                        "ignore_case": {
                            "type": "boolean",
                            "description": "是否忽略大小写",
                            "default": True
                        }
                    },
                    "required": ["file_path", "pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "在目录中搜索文件名或内容。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "搜索目录"
                        },
                        "pattern": {
                            "type": "string",
                            "description": "搜索模式（文件名或内容）"
                        },
                        "search_type": {
                            "type": "string",
                            "description": "搜索类型",
                            "enum": ["name", "content", "both"],
                            "default": "name"
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "文件过滤模式（如*.py）"
                        }
                    },
                    "required": ["directory", "pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "find_files",
                "description": "查找匹配模式的文件。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "搜索目录"
                        },
                        "pattern": {
                            "type": "string",
                            "description": "文件名模式（支持通配符*和?）"
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "最大搜索深度"
                        }
                    },
                    "required": ["directory", "pattern"]
                }
            }
        },
        # ==================== 网络搜索 ====================
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "搜索互联网获取实时信息。使用用户的浏览器执行搜索，返回标题、摘要和URL。这是联网搜索的唯一推荐方式，禁止使用代码执行来模拟HTTP请求或爬取网页。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询（尽量具体）"
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": "在浏览器中导航到指定URL并获取页面内容。适用于查看特定网页的详细信息。需要先使用 web_search 确定目标URL。每轮最多3次。禁止遍历URL、翻页或批量访问。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "要访问的完整URL（必须以 http:// 或 https:// 开头）"
                        },
                        "extract_links": {
                            "type": "boolean",
                            "description": "是否提取页面中的链接列表",
                            "default": False
                        }
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "browser_download",
                "description": "通过浏览器下载文件。支持文档(pdf/doc/xlsx)、图片(png/jpg)、压缩包(zip/rar)等常见文件类型。禁止下载网页文件(html/php)。每轮最多2个文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "文件的直接下载URL"
                        },
                        "filename": {
                            "type": "string",
                            "description": "保存的文件名（可选，包含扩展名）"
                        }
                    },
                    "required": ["url"]
                }
            }
        },
        # ==================== 进程管理 ====================
        {
            "type": "function",
            "function": {
                "name": "kill_process",
                "description": "终止进程。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pid": {
                            "type": "integer",
                            "description": "进程ID"
                        },
                        "force": {
                            "type": "boolean",
                            "description": "强制终止",
                            "default": False
                        }
                    },
                    "required": ["pid"]
                }
            }
        },
        # ==================== 环境变量 ====================
        {
            "type": "function",
            "function": {
                "name": "set_env_var",
                "description": "设置环境变量。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "环境变量名"
                        },
                        "value": {
                            "type": "string",
                            "description": "环境变量值"
                        }
                    },
                    "required": ["name", "value"]
                }
            }
        },
        # ==================== 技能管理 ====================
        {
            "type": "function",
            "function": {
                "name": "list_skills",
                "description": "列出所有可用的技能。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": "加载指定技能的完整内容。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "技能名称"
                        }
                    },
                    "required": ["name"]
                }
            }
        },
        # ==================== Brief 消息 ====================
        {
            "type": "function",
            "function": {
                "name": "brief",
                "description": "发送简短消息给用户并结束当前任务。这是最终回复工具，调用此工具后不应再调用其他工具。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "消息内容（支持Markdown）"
                        },
                        "attachments": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "附件文件路径列表"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["normal", "proactive"],
                            "description": "消息状态：normal=回复用户，proactive=主动通知",
                            "default": "normal"
                        }
                    },
                    "required": ["message"]
                }
            }
        },
        # ==================== Plan Mode ====================
        {
            "type": "function",
            "function": {
                "name": "enter_plan_mode",
                "description": "进入计划模式，用于设计实现方案。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "exit_plan_mode",
                "description": "退出计划模式。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "plan_title": {
                            "type": "string",
                            "description": "计划标题"
                        },
                        "plan_description": {
                            "type": "string",
                            "description": "计划描述"
                        },
                        "steps": {
                            "type": "array",
                            "description": "计划步骤",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string"},
                                    "details": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "create_plan",
                "description": "创建详细实现计划。写操作，需要串行执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "计划标题"
                        },
                        "description": {
                            "type": "string",
                            "description": "计划描述"
                        },
                        "steps": {
                            "type": "array",
                            "description": "计划步骤",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "description": {"type": "string"},
                                    "details": {"type": "string"}
                                }
                            }
                        }
                    },
                    "required": ["title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_plan",
                "description": "获取当前计划详情。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        # ==================== Tool Search ====================
        {
            "type": "function",
            "function": {
                "name": "tool_search",
                "description": "搜索可用工具。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询或 'select:<tool_name>' 直接选择"
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "最大返回结果数",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_tools",
                "description": "列出所有可用工具。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        # ==================== LSP 代码智能（9 种操作） ====================
        {
            "type": "function",
            "function": {
                "name": "lsp_go_to_definition",
                "description": "LSP: 跳转到符号的定义位置。需要指定文件路径和光标位置（行列号均从1开始）。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径（绝对路径或相对于项目目录的路径）"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始，如编辑器中显示的行号）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始，如编辑器中的列号）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_find_references",
                "description": "LSP: 查找符号的所有引用位置。返回按文件分组的引用列表。需要指定文件路径和光标位置。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径（绝对路径或相对于项目目录的路径）"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_hover",
                "description": "LSP: 获取光标处符号的类型信息、文档注释和函数签名。需要指定文件路径和光标位置。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_document_symbol",
                "description": "LSP: 获取文件的符号大纲（层次结构），包括类、方法、函数、变量等及其嵌套关系。只需指定文件路径。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_workspace_symbol",
                "description": "LSP: 在整个项目中搜索符号（跨文件）。支持模糊匹配符号名称。query为空时返回所有符号。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询（符号名称，支持模糊匹配）。留空返回所有项目符号。",
                            "default": ""
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_go_to_implementation",
                "description": "LSP: 跳转到接口/抽象方法的实际实现位置。对于类方法，查找所有实现了该方法的子类位置。需要指定文件路径和光标位置。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_prepare_call_hierarchy",
                "description": "LSP: 准备调用层次分析。返回指定位置函数/方法的调用层次入口项。用于后续的lsp_incoming_calls和lsp_outgoing_calls分析。需要指定文件路径和光标位置。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_incoming_calls",
                "description": "LSP: 获取传入调用——哪些函数/方法调用了指定位置的函数。显示每个调用者的位置和具体的调用点。需要指定文件路径和光标位置。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "lsp_outgoing_calls",
                "description": "LSP: 获取传出调用——指定位置的函数/方法调用了哪些其他函数。显示每个被调用者的位置和具体的调用点。需要指定文件路径和光标位置。只读操作，可以并发执行。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "文件路径"
                        },
                        "line": {
                            "type": "integer",
                            "description": "行号（从1开始）"
                        },
                        "character": {
                            "type": "integer",
                            "description": "列号/字符位置（从1开始）"
                        }
                    },
                    "required": ["file_path", "line", "character"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "usernote_write",
                "description": "读取或修改用户便签 (usernote) 的内容。usernote 是用户用来记录重要上下文、决策、待办事项的便签。调用此工具时会覆盖全部内容。如果不确定当前内容，先读文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "便签的完整新内容。会完全替换旧内容。"
                        }
                    },
                    "required": ["content"]
                }
            }
        },
    ]


def get_tools_definition_with_mcp(
    include_mcp: bool = True,
    mcp_manager=None,
    include_extensions: bool = True
) -> List[Dict[str, Any]]:
    """
    获取完整工具定义（包含 MCP 工具和扩展工具）
    
    Args:
        include_mcp: 是否包含 MCP 工具
        mcp_manager: MCP 管理器实例（可选，默认从全局获取）
        include_extensions: 是否包含扩展工具
    
    Returns:
        合并后的工具定义列表（顺序：base + MCP + extensions）
    """
    base_tools = get_tools_definition()
    
    # 合并 MCP 工具（优先）
    if include_mcp:
        try:
            from multi_agents.mcp.mcp_integration import register_mcp_tools
            base_tools = register_mcp_tools(base_tools)
        except ImportError as e:
            logger.warning(f"[ToolDef] MCP integration not available: {e}")
        except Exception as e:
            logger.error(f"[ToolDef] Failed to merge MCP tools: {e}")
    
    # 合并扩展工具
    if include_extensions:
        try:
            from multi_agents.extensions_loader import discover_extension_tools
            extension_tools = discover_extension_tools()
            if extension_tools:
                base_tools = base_tools + extension_tools
                logger.info(f"[ToolDef] Loaded {len(extension_tools)} extension tools")
        except ImportError as e:
            logger.warning(f"[ToolDef] Extension loader not available: {e}")
        except Exception as e:
            logger.error(f"[ToolDef] Failed to merge extension tools: {e}")
    
    return base_tools
