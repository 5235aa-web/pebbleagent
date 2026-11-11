#!/usr/bin/env python3
"""CKB coverage audit: files on disk against rows in the knowledge base.

Reads only. Enumerates, compares, prints. Changes nothing.

    python skills/ckb/scripts/ckb_audit.py --root <project root>

Reports three numbers:

    missing  on disk, absent from every chapter's index   (defect)
    extra    in an index, absent from disk                (lie, worse than missing)
    stale    file newer than the knowledge base           (hint, not proof)

Exit code is 0 only when missing and extra are both empty.
"""
import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT_EXCLUDES = {
    "__pycache__", "node_modules", "dist", "build", "target", "out",
    ".git", ".pebble", ".venv", "venv", "env", ".idea", ".vscode",
    "knowledgesource", "skills", "vendor", ".pytest_cache", ".ruff_cache",
    # Generated artefacts and single-file prototypes: not source modules.
    "brag-output-*", "design-preview", "history", "assets",
}
DEFAULT_EXTS = (".py", ".ts", ".tsx", ".vue", ".js", ".mjs", ".kt", ".java", ".rs", ".go", ".sh")

ROW_RE = re.compile(r"^\|\s*`?([^|`]+?)`?\s*\|")
OWNER_RE = re.compile(r"^(\d{2}-[\w\-]+)\s*[—\-:]\s*(.+?)\s*$")
# Header cells and separator rows must not be mistaken for paths.
HEADER_WORDS = {"file", "files", "path", "name", "module", "component"}


def is_path_cell(value: str) -> bool:
    if not value or value.lower() in HEADER_WORDS:
        return False
    if set(value) <= {"-", " ", ":"}:
        return False
    # An index cell must name a file: it needs an extension. "usernote / quick-notes"
    # is prose in a path-shaped cell, not a path.
    return "." in os.path.basename(value)


def parse_rows(kb_dir):
    """{chapter file name: set of file paths listed in its index}."""
    rows = {}
    for path in sorted(kb_dir.glob("*.md")):
        listed = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ROW_RE.match(line.strip())
            if not m:
                continue
            value = m.group(1).replace("\\", "/").strip()
            if is_path_cell(value):
                listed.add(value)
        rows[path.name] = listed
    return rows


def parse_ownership(content_md):
    """{chapter stem: path prefix} from the ## Ownership block, or {}."""
    if not content_md.exists():
        return {}
    owners = {}
    inside = False
    for line in content_md.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## ownership"):
            inside = True
            continue
        if inside and stripped.startswith("#"):
            break
        if not inside or not stripped:
            continue
        m = OWNER_RE.match(stripped)
        if not m:
            continue
        chapter, scope = m.group(1), m.group(2)
        scope = scope.strip().rstrip("`").lstrip("`")
        if scope.lower().startswith("external"):
            # Indexed from a repository that is not this one: nothing on disk to
            # compare against, so the chapter is recorded as unauditable.
            owners[chapter] = None
            continue
        if "/**" in scope:
            owners[chapter] = scope.split("/**")[0].rstrip("/")
    return owners


def _excluded(name: str, excludes) -> bool:
    for pattern in excludes:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return True
        elif name == pattern:
            return True
    return False


SCOPE_RE = re.compile(r"^>\s*Scope:\s*`?([^`·|]+?)/\*\*", re.I)


def parse_scopes(kb_dir):
    """{chapter file name: path prefix} from each chapter's '> Scope:' line.

    A chapter that declares its root may write short paths; resolving them under
    that root turns `ThemeSheet.vue` into `frontend/src/components/ThemeSheet.vue`
    without asking the author to repeat the directory on every row.
    """
    scopes = {}
    for path in sorted(kb_dir.glob("*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:12]:
            m = SCOPE_RE.match(line.strip())
            if m:
                scopes[path.name] = m.group(1).strip().rstrip("/")
                break
    return scopes


def walk_files(root, exts, excludes):
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not _excluded(d, excludes) and not d.startswith(".")]
        rel_base = os.path.relpath(base, root).replace("\\", "/")
        rel_base = "" if rel_base == "." else rel_base
        for name in files:
            if name.lower().endswith(tuple(exts)):
                found.append(f"{rel_base}/{name}" if rel_base else name)
    return sorted(found)


def chapter_for(path, owners, chapter_files):
    """Which chapter should own this file, by longest matching prefix."""
    best, best_len = None, -1
    for chapter, prefix in owners.items():
        if not prefix or not path.startswith(prefix + "/"):
            continue
        if len(prefix) > best_len:
            best, best_len = chapter, len(prefix)
    if best:
        for name in chapter_files:
            if name.startswith(best):
                return name
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="CKB coverage audit (read-only)")
    ap.add_argument("--root", default=".", help="project root")
    ap.add_argument("--kb", default="knowledgesource", help="knowledge base directory")
    ap.add_argument("--ext", nargs="*", default=list(DEFAULT_EXTS), help="file extensions to audit")
    ap.add_argument("--exclude", nargs="*", default=sorted(DEFAULT_EXCLUDES), help="directory names to skip")
    ap.add_argument("--quiet-stale", action="store_true", help="do not list stale files")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    kb_dir = root / args.kb
    if not kb_dir.is_dir():
        print(f"no knowledge base at {kb_dir}")
        return 2

    rows = parse_rows(kb_dir)
    owners = parse_ownership(kb_dir / "content.md")
    # Chapters declared external describe a repository that is not this one: their
    # rows cannot be found on disk, and calling them extra would be a false alarm.
    external_rows = set()
    for chapter, listed in rows.items():
        stem = chapter[:-3] if chapter.endswith(".md") else chapter
        if stem in owners and owners[stem] is None:
            external_rows |= set(listed)

    indexed = set()
    for listed in rows.values():
        indexed |= listed
    auditable = indexed - external_rows

    exts = {e if e.startswith(".") else "." + e for e in args.ext}
    on_disk = set(walk_files(root, sorted(exts), set(args.exclude)))

    # A row written as a bare file name ("context_compressor.py") resolves by
    # name only. It counts as covered and is reported as weak: the index knows
    # the file exists but not where it lives, so the next reader still searches.
    full_paths = {p for p in auditable if "/" in p}
    bare_names = {p for p in auditable if "/" not in p}
    scopes = parse_scopes(kb_dir)
    covered, weak = set(), set()
    for rel in on_disk:
        if rel in full_paths:
            covered.add(rel)
    # A short path inside a chapter that declared its root resolves under that
    # root, provided exactly one file matches. That is what the scope line is for.
    for chapter, listed in rows.items():
        stem = chapter[:-3] if chapter.endswith(".md") else chapter
        if stem in owners and owners[stem] is None:
            continue
        scope = scopes.get(chapter)
        if not scope:
            continue
        for name in listed:
            if "/" in name:
                continue
            hits = [p for p in on_disk if p.startswith(scope + "/") and os.path.basename(p) == name]
            if len(hits) == 1:
                covered.add(hits[0])
    # Anything still unmatched by name is weak: the row exists but does not say where.
    for rel in on_disk:
        if rel in covered:
            continue
        # A file at the project root is already named by its path: "run_backend.py"
        # is what the reader would type, not a short form of something deeper.
        if "/" not in rel and rel in bare_names:
            covered.add(rel)
            continue
        if os.path.basename(rel) in bare_names:
            covered.add(rel)
            weak.add(rel)

    missing = sorted(on_disk - covered)
    # `extra` only applies to extensions this audit scans. An indexed .exe or
    # .json is a real thing the knowledge base should describe; it is simply not
    # a source file, and calling it extra would be a false alarm.
    audited = tuple(sorted(exts))
    extra = sorted(p for p in (full_paths - on_disk) if p.lower().endswith(audited))

    newest_kb = max((p.stat().st_mtime for p in kb_dir.glob("*.md")), default=0)
    stale = []
    for rel in sorted(on_disk & indexed):
        try:
            if (root / rel).stat().st_mtime > newest_kb:
                stale.append(rel)
        except OSError:
            pass

    print(f"CKB audit: {root}")
    print(f"  chapters {len(rows)}  indexed {len(indexed)}  external {len(external_rows)}"
          f"  on disk {len(on_disk)}  weak {len(weak)}"
          f"  ownership {'yes' if owners else 'no (no ## Ownership block)'}")
    if weak:
        print("  weak = listed by bare file name only; the index does not say where it lives")

    if missing:
        print(f"\nmissing ({len(missing)})")
        groups = {}
        for rel in missing:
            groups.setdefault(chapter_for(rel, owners, rows.keys()) or "(unowned)", []).append(rel)
        for chapter in sorted(groups):
            print(f"  -> {chapter}")
            for rel in groups[chapter]:
                print(f"       {rel}")
    if extra:
        print(f"\nextra ({len(extra)})")
        for rel in extra:
            print(f"       {rel}")
    if stale and not args.quiet_stale:
        print(f"\nstale ({len(stale)})  files changed after the knowledge base was last written")
        for rel in stale[:20]:
            print(f"       {rel}")
        if len(stale) > 20:
            print(f"       ... and {len(stale) - 20} more")

    ok = not missing and not extra
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}  "
          f"missing={len(missing)} extra={len(extra)} weak={len(weak)} stale={len(stale)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
