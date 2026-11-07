"""System prompt assembly."""


def render_project_path_instruction(project_path: str) -> str:
    return f"""

[CRITICAL: PROJECT WORKING DIRECTORY]
Your working directory (the project folder) is: {project_path}

You MUST obey every rule below:
1. All file operations stay inside this directory by default.
2. Relative paths resolve against this directory.
3. You MUST NOT read, write, list, edit, or execute anything outside this directory unless the user has explicitly named that path in this conversation.
4. Shell commands run inside this directory by default.
5. The one and only exception: the user explicitly names another path. When that happens, execute against that path at once. Do not refuse, do not demand repeated confirmation, do not stall.

Beyond that exception, reaching outside this directory is a violation. Only an explicit path from the user authorizes it. Absent such a path, treat this directory as a hard boundary, not a suggestion.

Current working directory: {project_path}
"""


def render_usernote_prompt(content: str) -> str:
    if not content:
        return ""
    return f"""

<usernote>
{content}
</usernote>"""


def compose_system_prompt(base: str, experiences: str, project_path: str, usernote: str) -> str:
    # The experience block goes last on purpose: it is the only section that can
    # change between turns, and the provider caches the prompt by prefix. Placed
    # earlier, every change would also invalidate the tool schemas and the whole
    # message history that follow it.
    return (
        base
        + render_project_path_instruction(project_path)
        + render_usernote_prompt(usernote)
        + (f"\n\n{experiences}" if experiences else "")
    )
