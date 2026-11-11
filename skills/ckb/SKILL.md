---
name: ckb
description: "Code knowledge base: detect, build, and maintain a chaptered project knowledge base; read the index first, then the relevant chapters."
---

# CKB — Code Knowledge Base

> Code knowledge base: detect the project knowledge base at the start of a conversation, build it first if missing, then work.
> Structure is a directory plus chapter files: read `content.md` first to locate chapters, then read only the chapters you need.
> Full specification: `CKB_SPEC.md`.
> **Document positioning**: chapters are current-state facts (present tense, objective, neutral): describe what is true now, never the edit that produced it. `VERSION` is the change log, but a terse one — each entry states what changed and what the result is, not how the work went.

---

## Trigger Rules (highest priority)

**At the start of every conversation, run detection immediately after the first valid task request:**

```
1. read_file "knowledgesource/content.md" (relative to the project root) — the read itself is the probe
2. Decide:
   ├─ Read succeeds → chapter list available → read the relevant chapter files → continue
   ├─ File not found → fall back to the legacy single-file layout:
   │    read_file "knowledgesource/PROJECT_GRAPH.md"
   │    ├─ Succeeds → legacy format (v1.x): use it as the authoritative reference and tell the user
   │    │         "the knowledge base is in the legacy single-file format; consider migrating to directory + chapter files"
   │    └─ Fails → no knowledge base → ask the user whether to build one; enter build mode on approval (see below)
   └─ Other errors → report to the user
```

**📖 Read on demand**: detection reads only the directory file (`content.md`), not every chapter. During work, use the per-chapter summary in the directory to locate the relevant chapter and read only that file (e.g. `03-Gateway-System.md`). Read multiple chapters only when the task needs a global view (e.g. a full refactor).

**⚠️ Never use glob to detect the knowledge base**: in this environment glob under-reports directory-prefixed relative patterns (e.g. `knowledgesource/content.md`, `knowledgesource/*`) — it can return zero results while the file exists, which would falsely trigger a rebuild. Always probe with `read_file` or `get_file_info`.

## Build Mode Constraints

While building, it is **strictly forbidden** to:

- answer task requests
- run code (execute_code / execute_shell)
- modify files (write_file / edit_file) — except the knowledge base files being generated
- judge or speculate about the project code

**Allowed operations only**: index, read, trace, and generate knowledge base files (`content.md` + chapter files + `VERSION`).

---

## Build Pipeline (6 stages)

### Stage 1: Full index

```
mcp__codebase_memory__index_repository(repo_path=<project root>, mode="full")
```

Wait for completion; record `total_nodes` + `total_edges`.

### Stage 2: Architecture extraction

```
mcp__codebase_memory__get_architecture(project, aspects=["all"])
```

Extract: languages, clusters, hotspots, boundaries.

### Stage 3: File enumeration and classification

```
glob "**/*.py" "**/*.ts" "**/*.vue" "**/*.json" "**/*.yaml" "**/*.md"
```

Exclude: `__pycache__/`, `node_modules/`, `dist/`, `.git/`, `.pebble/snapshots/`.

Classify: entry / core service / tool / data layer / configuration / types / test / stub / deprecated.

### Stage 4: Relationship tracing

For key calls of each core module:

```
mcp__codebase_memory__trace_path(project, function_name, direction="both", depth=2)
```

Use `depth=3` for high-fan-in hotspots.

### Stage 5: Sampling confirmation

- Pick 1-2 files per directory and read the first 30 lines to confirm
- Entry files, `__init__.py`, and YAML configuration must be read in full
- Cross-check: index results vs actual code

### Stage 6: Generate the knowledge base

Plan the chapter structure, generate chapters, then the directory:

```
1. Plan chapters (5-15):
   - Standalone global chapters: 01-Project-Overview; 02-Module-Topology
   - One chapter per functional system: based on clusters + directory layout + actual responsibilities
   - File index, critical paths, and boundary notes go into the owning system chapter
2. Generate chapter files: knowledgesource/{NN}-{Chapter-Name}.md
   - Name: English Title Case, hyphen-separated (e.g. Gateway-System)
   - Index: two decimal digits (01, 02, ... 10, 11, 12 ...)
   - Shared chapter format: file index (table), critical paths (numbered steps), topology diagram (ASCII), boundary notes (grouped)
3. Generate the directory file: knowledgesource/content.md
4. Write knowledgesource/VERSION
```

**content.md format**:

```
# {Project} Code Knowledge Base — Directory

> Last updated YYYY-MM-DD | {change summary} | {statistics}

01-Project-Overview (positioning, stack, entry points)
02-Module-Topology (module layers and call relationships)
03-{System-Name} ({one-line summary})
...

> Reading guide: read this directory first, then open only the chapters you need.
```

**Knowledge base layout**:

```
knowledgesource/
├── content.md          # directory (read this first)
├── 01-Project-Overview.md
├── 02-Module-Topology.md
├── 03-{System-Name}.md
├── ...
└── VERSION             # change log
```

---

## File Index Table

| File | Function | Key symbols | Callers | Callees | Tag |

Tags: 🔴Core / 🟡Aux / ⚠️Stub / 🗑️Deprecated / 📦Config / 🧪Test

Rows are tiered (spec `3.6.1`). Tier A fills all six columns; Tier B may drop `Callers`/`Callees`; Tier C keeps only `File / Function / Tag`. Tiering changes how much a row says, **never whether it exists**: the row count of a chapter's index must equal the number of files that chapter owns. A file you consider uninteresting still gets a row.

## Verification Checklist (must pass before marking complete)

- [ ] content.md generated; chapter list matches chapter files (count and names)
- [ ] Every chapter file exists and is non-empty
- [ ] Coverage audit reports missing = 0 and extra = 0 (`python skills/ckb/scripts/ckb_audit.py --root <project root>`)
- [ ] Every chapter opens with a scope declaration whose numbers are current
- [ ] At least one file per directory sampled and confirmed
- [ ] Every entry file has a corresponding step in the critical paths
- [ ] Files tagged "⚠️Stub" confirmed with read_file
- [ ] Every module in the topology table exists in the file index
- [ ] Function names in critical paths verifiable via search_knowledge base
- [ ] VERSION written

## Sync Obligation

After every project change (files added/modified/deleted, bug fixes, new features, architecture changes):

```
1. Read knowledgesource/content.md to locate the affected chapters
2. Files added or deleted: add or remove their index rows first. A new file belongs to the chapter owning its path prefix (spec `3.6.2`); a deleted file loses its row in the same pass. This is its own step, not part of "updating entries": entry updates describe files that already have rows, so a new file is invisible to that wording.
3. Update the affected entries in those chapters (Function, Key symbols, critical paths, boundary notes)
4. Recompute the chapter's scope declaration when the file count moved
5. If chapters were added/removed: update the content.md directory and rename chapter files per the naming rule (keep indices contiguous)
6. Append one terse entry to knowledgesource/VERSION: what changed and the resulting state, not the process
```

### Backfill on a miss

When a lookup in the knowledge base fails and you then find the file by directory structure or search, add its row to the owning chapter before continuing the task. Tier B is enough. Record one line in VERSION.

An index that only ever lags behind the code degrades monotonically, and no amount of "we should audit this someday" reverses it. Backfilling on the miss costs one row and keeps the index growing with the code instead of behind it.

### When to audit

| Event | Action |
|-------|--------|
| Conversation start | probe `content.md`; no audit needed |
| Code changed | incremental update, including the added or deleted rows |
| Lookup missed | backfill the row |
| Build completed | full audit; missing and extra must be 0 |
| Before a release | full audit |
| User asks | full audit |

Drive any periodic sweep from the VERSION entry count rather than from a date. An agent has no reliable clock; a count of change records is deterministic.
