"""GitHub repository browsing over the REST API.

The panel never talks to api.github.com itself. The renderer's CSP keeps it on
localhost, and a personal access token has no business living in a process that
also renders agent-authored HTML into a sandboxed iframe. Every call is proxied
here instead, and the token is borrowed from the MCP entry the user already
filled in rather than asked for twice.

Responses are cached by ETag. Paging through a repository costs a handful of
requests per screen, and an authenticated PAT gets 5000 an hour; without the
conditional request a few minutes of browsing would spend it.
"""

import json
import logging
import os
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
_UA = "PebbleAgent"
_TIMEOUT = 12
_CACHE_LIMIT = 80

# The contents API refuses to hand back a blob over 1 MB, and a panel-width
# viewer would not be the place to read one anyway.
_MAX_FILE_BYTES = 1024 * 1024

# A repository name goes into a URL path, so it is validated rather than
# escaped: a value like "owner/../../user" must never reach the API.
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# url -> {"etag": str, "body": bytes}; _CACHE_ORDER keeps the oldest first so
# the dict can be trimmed without an LRU implementation.
#
# Every github.* call runs in the thread pool, so these two are touched from
# several threads at once. The lock is held only around the dict work — never
# across the request — so concurrent fetches still overlap.
_CACHE: dict = {}
_CACHE_ORDER: list = []
_CACHE_LOCK = threading.Lock()

# `force` skips the ETag cache for one request. It rides on a thread-local
# rather than a parameter because the flag has to reach every `_fetch` a
# handler makes — a refresh touches the metadata, the README, the languages,
# the tree and three lists — and threading it through each call site would add
# a parameter to eight functions for one button. Each request gets its own
# thread from the pool, and `handle` overwrites the flag on entry, so a reused
# thread cannot inherit the previous request's value.
_LOCAL = threading.local()


def _force() -> bool:
    return bool(getattr(_LOCAL, "force", False))


# ---------------------------------------------------------------- credentials


def _mcp_paths(srv) -> list:
    """Where a user-level .mcp.json can live, most specific first."""
    paths = []
    data_dir = getattr(srv, "_DATA_DIR", None)
    if data_dir:
        paths.append(Path(data_dir) / ".mcp.json")
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    paths.append(Path(home) / ".pebble" / ".mcp.json")
    return paths


def _read_token(srv) -> str:
    """The PAT the github MCP server already carries.

    Only a github entry is considered, and only its GITHUB_* variables: any
    token in the file would hand this module a credential for some other
    service. The placeholder shipped in the bundled config is rejected too,
    because a 401 from a literal "ghp_xxxx" reads like an auth bug.
    """
    for path in _mcp_paths(srv):
        try:
            if not path.exists():
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"[GitHub] cannot read {path}: {e}")
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            continue
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            is_github = "github" in str(name).lower()
            env = entry.get("env")
            if not isinstance(env, dict):
                continue
            for key, value in env.items():
                if not isinstance(value, str) or not value.strip():
                    continue
                upper = key.upper()
                if not (upper.startswith("GITHUB_") or (is_github and upper.endswith("TOKEN"))):
                    continue
                token = value.strip()
                if "xxxx" in token.lower() or token.upper().startswith("REPLACE"):
                    continue
                return token
    return ""


# -------------------------------------------------------------------- transport


def _request(token: str, url: str, accept: str, extra: dict):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _UA,
    }
    headers.update(extra or {})
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers
    except Exception as exc:
        return 0, str(exc).encode("utf-8", "replace"), {}


def _trim_cache() -> None:
    while len(_CACHE_ORDER) > _CACHE_LIMIT:
        _CACHE.pop(_CACHE_ORDER.pop(0), None)


def _fetch(token: str, path: str, params: dict = None, accept: str = "application/vnd.github+json"):
    """GET with an ETag round trip. Returns (ok, status, body, headers)."""
    url = _API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    cached = None
    if not _force():
        with _CACHE_LOCK:
            cached = _CACHE.get(url)
    extra = {"If-None-Match": cached["etag"]} if cached else {}
    status, body, headers = _request(token, url, accept, extra)
    if status == 304 and cached:
        return True, 200, cached["body"], headers
    if status == 200:
        etag = (headers or {}).get("ETag")
        if etag:
            with _CACHE_LOCK:
                # Two threads can miss the same url and both store it; appending
                # twice would leave a stale entry in the trim order.
                if url not in _CACHE:
                    _CACHE_ORDER.append(url)
                _CACHE[url] = {"etag": etag, "body": body}
                _trim_cache()
        return True, 200, body, headers
    return False, status, body, headers


def _explain(status: int, body: bytes) -> str:
    """One sentence the panel can show instead of a raw status code."""
    if status == 0:
        detail = body[:120].decode("utf-8", "replace") if body else ""
        return f"Cannot reach api.github.com ({detail or 'network error'})"
    if status == 401:
        return "GitHub rejected the token (401). Check GITHUB_PERSONAL_ACCESS_TOKEN in the MCP panel"
    if status == 403:
        return "GitHub refused the request (403). Rate limit reached or the token lacks the scope"
    if status == 404:
        return "Not found (404). The repository may be private or renamed"
    return f"GitHub returned {status}"


def _json(body: bytes):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None


# ------------------------------------------------------------ local checkout

# A remote can be written four ways and all four are in the wild. Every pattern
# is anchored on github.com, so a GitLab or self-hosted remote yields nothing
# rather than a wrong repository name.
_REMOTE_PATTERNS = (
    re.compile(r"^https?://(?:[^@/]+@)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$"),
    re.compile(r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"),
)


def _remote_repo(root: Path) -> str:
    """`owner/name` for the checkout's origin remote, or an empty string.

    Read from .git/config rather than by running git: this is a read-only
    lookup on a directory the caller already owns, and spawning a process to
    parse one ini line would be a far larger surface for the same answer.
    """
    try:
        text = (root / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    # The section ends at the next bracket, so the url search is bounded
    # instead of matching the first url anywhere in the file.
    section = re.search(r'\[remote "origin"\](.*?)(?=\n\[|\Z)', text, re.S)
    if not section:
        return ""
    url = re.search(r"^\s*url\s*=\s*(.+?)\s*$", section.group(1), re.M)
    if not url:
        return ""
    raw = url.group(1).strip()
    for pattern in _REMOTE_PATTERNS:
        match = pattern.match(raw)
        if match:
            owner, name = match.group(1), match.group(2)
            if owner and name:
                return f"{owner}/{name}"
    return ""


def _head_branch(root: Path) -> str:
    """Current branch name; empty on a detached HEAD, which is not an error."""
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else ""


def _git(root: Path, args) -> str:
    """One read-only git command; any failure yields an empty string.

    A missing binary, an unreadable repository or a slow filesystem must never
    turn the panel into an error state — everything built on this is a badge.
    `GIT_TERMINAL_PROMPT=0` keeps a credential prompt from blocking the thread
    until the timeout fires.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(root), capture_output=True, timeout=3, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", "replace")


def _ahead_count(root: Path) -> int:
    """Commits reachable from HEAD but from no remote ref."""
    try:
        return max(0, int(_git(root, ["rev-list", "--count", "HEAD", "--not", "--remotes"]).strip() or "0"))
    except ValueError:
        return 0


def _dirty_count(root: Path) -> int:
    """Files carrying uncommitted work, modified and untracked alike.

    `ahead` alone answers "did I push", which is 0 for someone who has been
    editing all afternoon without committing — the case the panel was asked
    about and got wrong. The question is "what of mine is not on GitHub yet",
    and that covers the working tree as well as the branch.
    """
    return len([line for line in _git(root, ["status", "--porcelain"]).splitlines() if line.strip()])



def _rate(headers) -> dict:
    if not headers:
        return {}
    try:
        return {
            "limit": int(headers.get("X-RateLimit-Limit") or 0),
            "remaining": int(headers.get("X-RateLimit-Remaining") or 0),
            "reset": int(headers.get("X-RateLimit-Reset") or 0),
        }
    except Exception:
        return {}


# ------------------------------------------------------------------- shapers


def _repo_brief(item: dict) -> dict:
    return {
        "name": item.get("name") or "",
        "fullName": item.get("full_name") or "",
        "private": bool(item.get("private")),
        "description": item.get("description") or "",
        "language": item.get("language") or "",
        "stars": item.get("stargazers_count") or 0,
        "forks": item.get("forks_count") or 0,
        "openIssues": item.get("open_issues_count") or 0,
        "pushedAt": item.get("pushed_at") or item.get("updated_at") or "",
        "branch": item.get("default_branch") or "main",
        "url": item.get("html_url") or "",
        "topics": [t for t in (item.get("topics") or []) if isinstance(t, str)][:6],
        "size": item.get("size") or 0,
    }


def _commit_brief(item: dict) -> dict:
    commit = item.get("commit") or {}
    author = commit.get("author") or {}
    sha = item.get("sha") or ""
    # Both forms: the panel shows the short one and passes the full one back to
    # `github.commit`, where a seven-character prefix could in principle be
    # ambiguous.
    return {
        "sha": sha,
        "shortSha": sha[:7],
        "message": (commit.get("message") or "").split("\n")[0][:140],
        "author": author.get("name") or "unknown",
        "date": author.get("date") or "",
        "avatar": (item.get("author") or {}).get("avatar_url") or "",
    }


def _issue_brief(item: dict) -> dict:
    labels = []
    for label in item.get("labels") or []:
        if isinstance(label, dict) and label.get("name"):
            labels.append({"name": label["name"], "color": label.get("color") or ""})
    return {
        "number": item.get("number") or 0,
        "title": item.get("title") or "",
        "state": item.get("state") or "open",
        "user": (item.get("user") or {}).get("login") or "",
        "comments": item.get("comments") or 0,
        "updatedAt": item.get("updated_at") or "",
        "url": item.get("html_url") or "",
        "labels": labels[:3],
        "draft": bool(item.get("draft")),
    }


def _entry_brief(item: dict) -> dict:
    return {
        "name": item.get("name") or "",
        "path": item.get("path") or "",
        "type": "dir" if item.get("type") == "dir" else "file",
        "size": item.get("size") or 0,
    }


# ---------------------------------------------------------------------- handle


def _repo_or_error(params: dict, rid: str):
    """Validate the repository argument once, for every method that needs it."""
    repo = str(params.get("repo") or "").strip()
    if not _REPO_RE.match(repo):
        return None, {"id": rid, "error": {"code": -32602, "message": "repo must be owner/name"}}
    return repo, None


def handle(ctx, method):
    srv = ctx.srv
    params = ctx.params
    rid = ctx.rid
    _LOCAL.force = bool(params.get("force"))

    if method == "github.status":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "result": {
                "configured": False, "login": "", "name": "", "avatar": "",
                "repos": 0, "rate": {},
            }}
        ok, status, body, headers = _fetch(token, "/user")
        user = _json(body) if ok else None
        if not isinstance(user, dict):
            return {"id": rid, "result": {
                "configured": True, "login": "", "name": "", "avatar": "",
                "repos": 0, "rate": _rate(headers), "error": _explain(status, body),
            }}
        return {"id": rid, "result": {
            "configured": True,
            "login": user.get("login") or "",
            "name": user.get("name") or "",
            "avatar": user.get("avatar_url") or "",
            "repos": (user.get("public_repos") or 0) + (user.get("total_private_repos") or 0),
            "rate": _rate(headers),
        }}

    if method == "github.local":
        """Which repository the active project is a checkout of.

        The path comes from the client, so it is only ever used to read three
        files under it and to run one read-only git command with it as cwd.
        Nothing is written and no path outside it is touched.
        """
        project = str(params.get("project") or "").strip()
        if not project:
            # The renderer's project registry can land after the first status
            # answer, so a request that omits the path still has to work: the
            # gateway already knows which project every tool call is scoped to.
            project = str(srv._get_current_project_root() or "")
        root = Path(project) if project else None
        if not root or not root.is_dir():
            return {"id": rid, "result": {"repo": "", "branch": "", "ahead": 0}}
        repo = _remote_repo(root)
        if not repo:
            return {"id": rid, "result": {"repo": "", "branch": "", "ahead": 0}}
        return {"id": rid, "result": {
            "repo": repo,
            "branch": _head_branch(root),
            "ahead": _ahead_count(root),
            "dirty": _dirty_count(root),
        }}

    if method == "github.repos":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        page = max(1, int(params.get("page") or 1))
        per_page = min(100, max(1, int(params.get("perPage") or 30)))
        # /user/repos is the only listing that includes private repositories and
        # follows the token's own visibility; affiliation narrows it to the ones
        # the user actually owns or collaborates on.
        ok, status, body, headers = _fetch(token, "/user/repos", {
            "sort": "pushed", "direction": "desc",
            "per_page": per_page, "page": page,
            "affiliation": "owner,collaborator,organization_member",
        })
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        items = _json(body)
        if not isinstance(items, list):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}
        return {"id": rid, "result": {
            "repos": [_repo_brief(i) for i in items if isinstance(i, dict)],
            "page": page,
            "hasMore": len(items) >= per_page,
            "rate": _rate(headers),
        }}

    if method == "github.repo":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        ok, status, body, _ = _fetch(token, f"/repos/{repo}")
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        meta = _json(body)
        if not isinstance(meta, dict):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}

        # README and languages are decoration, not the answer: a repository
        # without a README must still render, so both failures stay silent.
        readme = ""
        ok_r, _, body_r, _ = _fetch(token, f"/repos/{repo}/readme", accept="application/vnd.github.raw")
        if ok_r:
            readme = body_r.decode("utf-8", "replace")[:12000]

        languages = {}
        ok_l, _, body_l, _ = _fetch(token, f"/repos/{repo}/languages")
        if ok_l:
            parsed = _json(body_l)
            if isinstance(parsed, dict):
                languages = {k: v for k, v in parsed.items() if isinstance(v, int) and v > 0}

        return {"id": rid, "result": {
            "repo": _repo_brief(meta),
            "readme": readme,
            "languages": languages,
        }}

    if method == "github.activity":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        ok, status, body, _ = _fetch(token, f"/repos/{repo}/stats/commit_activity")
        if status == 202:
            # GitHub computes this table on demand. The first request for a
            # repository answers 202 with an empty body and no cache headers,
            # and the answer is ready a few seconds later.
            return {"id": rid, "result": {"weeks": [], "pending": True}}
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        data = _json(body)
        if not isinstance(data, list):
            return {"id": rid, "result": {"weeks": [], "pending": False}}
        weeks = []
        for week in data[-52:]:
            if not isinstance(week, dict):
                continue
            days = week.get("days")
            if not isinstance(days, list) or len(days) != 7:
                continue
            try:
                weeks.append([max(0, int(d or 0)) for d in days])
            except (TypeError, ValueError):
                continue
        return {"id": rid, "result": {"weeks": weeks, "pending": False}}

    if method == "github.contents":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        path = str(params.get("path") or "").strip("/")
        quoted = urllib.parse.quote(path)
        ok, status, body, _ = _fetch(token, f"/repos/{repo}/contents/{quoted}" if quoted else f"/repos/{repo}/contents")
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        items = _json(body)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return {"id": rid, "error": {"code": -32000, "message": "not a directory"}}
        entries = [_entry_brief(i) for i in items if isinstance(i, dict)]
        # Directories first, then files, each alphabetically: a mixed list makes
        # the tree read as noise at this width.
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
        return {"id": rid, "result": {"entries": entries, "path": path}}

    if method == "github.file":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        path = str(params.get("path") or "").strip("/")
        if not path:
            return {"id": rid, "error": {"code": -32602, "message": "path required"}}
        if ".." in path.split("/"):
            return {"id": rid, "error": {"code": -32602, "message": "invalid path"}}
        ok, status, body, _ = _fetch(
            token,
            f"/repos/{repo}/contents/{urllib.parse.quote(path)}",
            accept="application/vnd.github.raw",
        )
        if not ok:
            # An oversized blob comes back as 403 with a JSON body naming the
            # limit, which the generic explainer would report as a scope
            # problem and send someone to reissue a perfectly good token.
            if status == 403 and b"1 MB" in body:
                return {"id": rid, "error": {"code": -32000,
                                             "message": "File is larger than 1 MB; open it on GitHub instead"}}
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        if len(body) > _MAX_FILE_BYTES:
            return {"id": rid, "error": {"code": -32000,
                                         "message": "File is larger than 1 MB; open it on GitHub instead"}}
        # A NUL byte in the head means binary. Decoding it would fill the panel
        # with replacement characters and read as a broken viewer.
        binary = b"\x00" in body[:8192]
        return {"id": rid, "result": {"file": {
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "size": len(body),
            "binary": binary,
            "text": "" if binary else body.decode("utf-8", "replace"),
        }}}

    if method == "github.commit":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        sha = str(params.get("sha") or "").strip()
        # Hex only, and length-bounded: the value goes straight into a URL path,
        # and GitHub would also accept a branch or tag name here, which is a ref
        # this panel has no business resolving.
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
            return {"id": rid, "error": {"code": -32602, "message": "commit sha required"}}
        ok, status, body, _ = _fetch(token, f"/repos/{repo}/commits/{sha}")
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        data = _json(body)
        if not isinstance(data, dict):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}
        stats = data.get("stats") or {}
        files = []
        truncated = False
        for entry in (data.get("files") or []):
            if not isinstance(entry, dict):
                continue
            patch = entry.get("patch") or ""
            # GitHub omits `patch` for binary files and for diffs past its own
            # limit, and says nothing about it; that silence is what the note in
            # the panel has to surface.
            if not patch and (entry.get("additions") or entry.get("deletions")):
                truncated = True
            files.append({
                "filename": entry.get("filename") or "",
                "status": entry.get("status") or "",
                "additions": entry.get("additions") or 0,
                "deletions": entry.get("deletions") or 0,
                "patch": patch[:20000],
                "noPatch": not patch,
            })
        if len(files) >= 300:
            truncated = True
        return {"id": rid, "result": {
            "sha": data.get("sha") or sha,
            "stats": {
                "additions": stats.get("additions") or 0,
                "deletions": stats.get("deletions") or 0,
                "total": stats.get("total") or 0,
            },
            "files": files,
            "truncated": truncated,
        }}

    if method == "github.commits":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        ok, status, body, _ = _fetch(token, f"/repos/{repo}/commits", {"per_page": 12})
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        items = _json(body)
        if not isinstance(items, list):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}
        return {"id": rid, "result": {"commits": [_commit_brief(i) for i in items if isinstance(i, dict)]}}

    if method == "github.issues":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        ok, status, body, _ = _fetch(token, f"/repos/{repo}/issues", {
            "state": "open", "per_page": 15, "sort": "updated",
        })
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        items = _json(body)
        if not isinstance(items, list):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}
        # /issues returns pull requests too; the panel has a tab for those.
        issues = [_issue_brief(i) for i in items
                  if isinstance(i, dict) and not i.get("pull_request")]
        return {"id": rid, "result": {"issues": issues}}

    if method == "github.pulls":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        ok, status, body, _ = _fetch(token, f"/repos/{repo}/pulls", {
            "state": "open", "per_page": 15, "sort": "updated", "direction": "desc",
        })
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        items = _json(body)
        if not isinstance(items, list):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}
        return {"id": rid, "result": {"pulls": [_issue_brief(i) for i in items if isinstance(i, dict)]}}

    if method == "github.pullfiles":
        token = _read_token(srv)
        if not token:
            return {"id": rid, "error": {"code": -32000, "message": "no GitHub token configured"}}
        repo, err = _repo_or_error(params, rid)
        if err:
            return err
        try:
            number = int(params.get("number") or 0)
        except (TypeError, ValueError):
            number = 0
        if number <= 0:
            return {"id": rid, "error": {"code": -32602, "message": "pull number required"}}
        ok, status, body, _ = _fetch(
            token, f"/repos/{repo}/pulls/{number}/files", {"per_page": 100},
        )
        if not ok:
            return {"id": rid, "error": {"code": -32000, "message": _explain(status, body)}}
        data = _json(body)
        if not isinstance(data, list):
            return {"id": rid, "error": {"code": -32000, "message": "unexpected response shape"}}
        files = []
        for item in data:
            if not isinstance(item, dict):
                continue
            patch = item.get("patch")
            files.append({
                "filename": str(item.get("filename") or ""),
                "status": str(item.get("status") or "modified"),
                "additions": int(item.get("additions") or 0),
                "deletions": int(item.get("deletions") or 0),
                "patch": patch if isinstance(patch, str) else "",
                # GitHub omits `patch` for binary files and for diffs above its
                # own limit. The panel has to say so rather than draw an empty
                # box that reads as "this file did not change".
                "noPatch": not isinstance(patch, str),
            })
        # 100 is the page cap: at that size there is very likely another page.
        truncated = len(files) >= 100 or any(f["noPatch"] for f in files)
        return {"id": rid, "result": {"files": files, "truncated": truncated}}

    return {"id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}
