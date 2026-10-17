"""Declarative tool dispatch table.

Each entry maps a tool name to its handler. Adding a tool means adding one
row here and one handler method in the matching domain module.
"""

from typing import Any, Callable, Dict

ToolHandler = Callable[[Any, Dict[str, Any], Any], Any]

TOOL_HANDLERS: Dict[str, ToolHandler] = {
    "clarify": lambda self, args, context: self._tool_clarify(args.get('questions', []), args.get('question_id')),
    "browser_download": lambda self, args, context: self._tool_browser_download(args.get('url', ''), args.get('filename', '')),
    "browser_navigate": lambda self, args, context: self._tool_browser_navigate(args.get('url', ''), args.get('extract_links', False)),
    "copy_file": lambda self, args, context: self._tool_copy_file(args.get('source', ''), args.get('destination', '')),
    "create_directory": lambda self, args, context: self._tool_create_directory(args.get('path', '')),
    "create_plan": lambda self, args, context: self._tool_create_plan(args.get('title', ''), args.get('description', ''), args.get('steps', [])),
    "delete_file": lambda self, args, context: self._tool_delete_file(args.get('path', ''), args.get('recursive', False)),
    "edit_file": lambda self, args, context: self._tool_edit_file(file_path=args.get('file_path', ''), operation=args.get('operation', ''), line_number=args.get('line_number'), content=args.get('content'), end_line=args.get('end_line'), old_string=args.get('old_string', ''), new_string=args.get('new_string', ''), replace_all=args.get('replace_all', False)),
    "enter_plan_mode": lambda self, args, context: self._tool_enter_plan_mode(),
    "execute_code": lambda self, args, context: self._tool_execute_code(args.get('code', ''), args.get('language', 'python'), context),
    "execute_shell": lambda self, args, context: self._tool_execute_shell(args.get('command', ''), args.get('timeout', 60), context),
    "exit_plan_mode": lambda self, args, context: self._tool_exit_plan_mode(args.get('plan_title'), args.get('plan_description'), args.get('steps', [])),
    "fetch_url": lambda self, args, context: self._tool_fetch_url(args.get('url', ''), args.get('timeout', 30)),
    "find_files": lambda self, args, context: self._tool_find_files(args.get('directory', ''), args.get('pattern', ''), args.get('max_depth')),
    "get_env_var": lambda self, args, context: self._tool_get_env_var(args.get('name', '')),
    "get_file_info": lambda self, args, context: self._tool_get_file_info(args.get('path', '')),
    "get_plan": lambda self, args, context: self._tool_get_plan(),
    "get_system_info": lambda self, args, context: self._tool_get_system_info(),
    "git_diff": lambda self, args, context: self._tool_git_diff(args.get('repo_path', '.'), args.get('target')),
    "git_log": lambda self, args, context: self._tool_git_log(args.get('repo_path', '.'), args.get('count', 10)),
    "git_status": lambda self, args, context: self._tool_git_status(args.get('repo_path', '.')),
    "glob": lambda self, args, context: self._tool_glob(pattern=args.get('pattern', ''), path=args.get('path')),
    "grep": lambda self, args, context: self._tool_grep(pattern=args.get('pattern', ''), path=args.get('path'), glob_pattern=args.get('glob'), output_mode=args.get('output_mode', 'files_with_matches'), case_insensitive=args.get('-i', False), show_line_numbers=args.get('-n', True), after_context=args.get('-A'), before_context=args.get('-B'), context=args.get('-C'), file_type=args.get('type'), head_limit=args.get('head_limit', 250)),
    "kill_process": lambda self, args, context: self._tool_kill_process(args.get('pid'), args.get('force', False)),
    "list_directory": lambda self, args, context: self._tool_list_directory(args.get('path', ''), args.get('show_hidden', False), args.get('recursive', False)),
    "list_processes": lambda self, args, context: self._tool_list_processes(args.get('filter')),
    "list_skills": lambda self, args, context: self._tool_list_skills(),
    "list_tools": lambda self, args, context: self._tool_list_tools(),
    "load_skill": lambda self, args, context: self._tool_load_skill(args.get('name', '')),
    "ls": lambda self, args, context: self._tool_ls(path=args.get('path', ''), ignore=args.get('ignore', [])),
    "move_file": lambda self, args, context: self._tool_move_file(args.get('source', ''), args.get('destination', '')),
    "lsp_document_symbol": lambda self, args, context: self._tool_lsp_document_symbol(args.get('file_path', '')),
    "lsp_find_references": lambda self, args, context: self._tool_lsp_find_references(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_go_to_definition": lambda self, args, context: self._tool_lsp_go_to_definition(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_go_to_implementation": lambda self, args, context: self._tool_lsp_go_to_implementation(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_hover": lambda self, args, context: self._tool_lsp_hover(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_incoming_calls": lambda self, args, context: self._tool_lsp_incoming_calls(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_outgoing_calls": lambda self, args, context: self._tool_lsp_outgoing_calls(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_prepare_call_hierarchy": lambda self, args, context: self._tool_lsp_prepare_call_hierarchy(args.get('file_path', ''), args.get('line', 0), args.get('character', 0)),
    "lsp_workspace_symbol": lambda self, args, context: self._tool_lsp_workspace_symbol(args.get('query', '')),
    "powershell": lambda self, args, context: self._tool_powershell(args.get('command', ''), args.get('timeout', 60000), context),
    "read_file": lambda self, args, context: self._tool_read_file(args.get('file_path', ''), args.get('start_line'), args.get('end_line')),
    "read_pdf": lambda self, args, context: self._tool_read_pdf(args.get('file_path', ''), args.get('pages'), args.get('ocr_language', 'ch_sim+en')),
    "search_files": lambda self, args, context: self._tool_search_files(args.get('directory', ''), args.get('pattern', ''), args.get('search_type', 'name'), args.get('file_pattern')),
    "search_in_file": lambda self, args, context: self._tool_search_in_file(args.get('file_path', ''), args.get('pattern', ''), args.get('ignore_case', True)),
    "set_env_var": lambda self, args, context: self._tool_set_env_var(args.get('name', ''), args.get('value', '')),
    "todo_read": lambda self, args, context: self._tool_todo_read(),
    "todo_write": lambda self, args, context: self._tool_todo_write(args.get('todos', [])),
    "tool_search": lambda self, args, context: self._tool_tool_search(args.get('query', ''), args.get('max_results', 5)),
    "usernote_write": lambda self, args, context: self._tool_usernote_write(args.get('content', '')),
    "write_file": lambda self, args, context: self._tool_write_file(args.get('file_path', ''), args.get('content', ''), args.get('mode', 'write')),
}
