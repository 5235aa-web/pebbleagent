# CKB (Code Knowledge Base) — Specification v2.0

> This document defines the complete CKB mechanism: detection, build, writing, indexing, verification, migration.
> **v2.0 core change**: the knowledge base moved from a single file (PROJECT_GRAPH.md) to a directory plus chapter files —
> `content.md` is the chapter directory, each chapter is its own file (e.g. `03-Gateway-System.md`),
> and both reading and updating locate chapters through the directory instead of loading one large file.
> The CKB skill file (SKILL.md) references this document as the technical specification.

---

## 1. Detection Logic (Gate Check)

### 1.1 When to run

The agent must run detection at the start of every conversation, right after receiving the first valid task request.

### 1.2 Detection steps

```
1. Confirm the project root (current working directory)
2. read_file "knowledgesource/content.md" (relative to the project root) — the read itself is the probe
3. Decide:
   ├─ Read succeeds → chapter directory available → read the relevant chapters on demand → proceed normally
   ├─ File not found → fall back to the legacy single-file layout (see 1.3)
   └─ Other errors → report to the user
```

**Read on demand (the v2.0 core rule)**: detection reads only the directory file (`content.md`), never the full chapters. During work, use the one-line summary in `content.md` (the parenthesised note) to locate the chapters relevant to the task and read only those. Read multiple chapters only when the task needs a global view (e.g. a full refactor or a project-wide audit).

**⚠️ Tool constraint (important)**: never use glob to detect the knowledge base. In this environment glob under-reports directory-prefixed relative patterns (e.g. `knowledgesource/content.md`, `knowledgesource/*`) — measured: it returns zero results while the file exists, which falsely triggers an unnecessary build. Always probe with `read_file` or `get_file_info` (both verified reliable).

### 1.3 Legacy format compatibility (v1.x → v2.0)

If `content.md` does not exist (File not found), fall back to the legacy single file PROJECT_GRAPH.md:

```
read_file "knowledgesource/PROJECT_GRAPH.md"
├─ Succeeds → legacy single-file format (v1.x):
│    1. Continue working with the legacy file as the authoritative reference (do not block the task)
│    2. Tell the user: "This project's knowledge base uses the legacy single-file format. Migrating to the new
│       format (directory + chapter files) allows reading chapters on demand instead of loading one large file.
│       Migrate now?"
│    3. User agrees → migrate per Chapter 6 (Migration)
└─ Fails → no knowledge base → ask the user whether to build one; on approval enter build mode (see Chapter 2)
```

### 1.4 Hard constraints in build mode

In build mode the following are **strictly forbidden**:

- answering task requests
- executing code
- calling write tools (write_file / edit_file / execute_shell, etc.) — except generating the knowledge base files themselves
- making any judgement or speculation about the project code

Allowed operations only: reading files, calling codebase_memory tools, and generating knowledge base files (`content.md` + chapter files + `VERSION`).

---

## 2. Build Pipeline

The build runs in 6 stages, in order; each stage completes before the next begins.

### Stage 1: Full index

**Goal**: import the project into the code knowledge graph database.

```
1. Call mcp__codebase_memory__index_repository(repo_path=<project root>, mode="full")
2. Wait for indexing to finish
3. Record the result: total_nodes, total_edges, detected languages
```

**Output**: index confirmation (node count + edge count).

**Time estimate**: a medium project (100-200 files) takes about 2-5 minutes.

### Stage 2: Architecture extraction

**Goal**: obtain the high-level architecture, module layers, and call relationships.

```
1. Call mcp__codebase_memory__get_architecture(project, aspects=["all"])
2. Extract from the result:
   - node_labels: node counts per type
   - edge_types: relationship types
   - languages: language distribution
   - clusters: community detection results (natural module groups)
   - hotspots: high-fan-in nodes (core modules)
   - boundaries: cross-module call heat
3. List all directories (list_directory on the root + each subdirectory)
```

**Output**: architecture overview (languages, module layers, hotspot nodes).

### Stage 3: File enumeration and classification

**Goal**: enumerate every source file and classify by type and importance.

```
1. glob "**/*.py" → all Python files
2. glob "**/*.ts" "**/*.vue" "**/*.tsx" → all frontend files
3. glob "**/*.json" "**/*.yaml" "**/*.toml" → configuration files
4. glob "**/*.md" (excluding knowledgesource/) → documentation

5. Classify each file:
   - entry file (contains a main/run/app entry function)
   - core service file (>500 lines, core classes/functions)
   - tool file (_tool.py / _tools.py / tool_*.py)
   - data layer file (db/database/session/store)
   - configuration loader (config/settings/env)
   - type definition file (types/schema/model)
   - test file (test_*/_test)
   - stub file (empty body or fixed None/False return)
   - deprecated file (in a deprecated directory or marked deprecated)
   - helper/script file

6. Excluded directories:
   - __pycache__/
   - node_modules/
   - dist/
   - .git/
   - .pebble/snapshots/
   - any generated/build output directory
```

**Output**: classified file inventory (by directory + category).

### Stage 4: Relationship tracing

**Goal**: trace call relationships for every key module.

```
1. For each core service file, pick the key functions (entry points, constructors, main methods)
2. Call mcp__codebase_memory__trace_path(project, function_name, direction="both", depth=2)
3. Record:
   - callers: who calls this function
   - callees: what this function calls
4. For each hotspot node (high fan-in), trace an extra depth=3 to capture the full chain
5. Build a module-level call matrix:
   - module A → module B (call count / relationship type)
```

**Output**: per-module call tables + a global call matrix.

### Stage 5: Sampling confirmation

**Goal**: sample files to confirm index accuracy and avoid knowledge base errors caused by indexing gaps.

```
1. Pick 1-2 files at random from each directory
2. read_file the head (first 30 lines) to confirm: module description, imports, main classes/functions
3. The following must be read in full (no sampling):
   - entry files (run_backend.py, ws_server.py, entry.py, main.py)
   - every __init__.py
   - every YAML configuration file
4. Cross-check against the index: confirm indexed symbols and call chains match the actual code
```

**Output**: sampling confirmation report (note and correct any mismatch).

### Stage 6: Generate the knowledge base

**Goal**: organise the information as a directory plus chapter files under `knowledgesource/`.

```
1. Plan the chapter structure (see 3.4 Chapter division principles):
   - Standalone global chapters: project overview, module topology
   - One chapter per functional system: clusters (community detection) + directory layout + actual responsibilities
2. Generate chapter files one by one: knowledgesource/{NN}-{Chapter-Name}.md
   - Each chapter contains: the system's file index (table) + related critical paths (numbered steps) + related boundary notes
3. Generate the directory file: knowledgesource/content.md (format in 3.2)
4. Write knowledgesource/VERSION (format: v{date}-{file_count}files-{node_count}nodes)
```

**Output**: content.md + all chapter files + VERSION update.

---

## 3. Writing Specification (Knowledge Base Format)

### 3.1 File structure

```
knowledgesource/
├── content.md               # directory: chapter list + one-line summary per chapter (read this first)
├── 01-Project-Overview.md   # global chapter
├── 02-Module-Topology.md    # global chapter
├── 03-{System-Name}.md      # functional system chapter
├── ...
└── VERSION                  # change log (single file, format unchanged)
```

### 3.2 content.md format

```
# {Project} Code Knowledge Base — Directory

> Last updated YYYY-MM-DD | {change summary} | {statistics}

01-Project-Overview (positioning, stack, entry points)
02-Module-Topology (module layers and call relationships)
03-{System-Name} ({one-line summary})
04-{System-Name} ({one-line summary})
...

> Reading guide: read this directory first, then open only the chapters you need (e.g. `03-Gateway-System.md`).
```

**Rules**:

- The parenthesised note after each chapter entry is the "necessary summary" (one line; omit if unnecessary)
- Chapter entries and chapter file names correspond one-to-one: an entry `03-Gateway-System` means the file `03-Gateway-System.md`
- Keep the header block: last update time, change summary, statistics (file count, etc.)

### 3.3 Chapter file naming rules

- Format: `{NN}-{Chapter-Name}.md`
- Index: two decimal digits (01, 02, ... 10, 11, 12 ...)
- Index and name are separated by a hyphen `-`
- Name: English Title Case, hyphen-separated (e.g. Project-Overview, Gateway-System); no spaces, no filesystem-reserved characters `< > : " / \ | ? *`
- When chapters are inserted or removed, rename the affected files to keep the directory ↔ files mapping one-to-one and the indices contiguous

### 3.4 Chapter division principles

- Global content gets its own chapters: "Project Overview" and "Module Topology" come first
- One chapter per functional system: based on clusters (community detection) + directory layout + actual responsibilities
- Ownership rule: file index, critical paths, and boundary notes belong to the chapter of the system they belong to
- Cross-system content: purely global flows (e.g. the overall startup sequence) go into "Project Overview" or "Module Topology"
- Chapter count: 5-15; consider splitting a chapter expected to exceed ~1000 lines (sub-modules become separate chapters)
- Example names: `03-Gateway-System`, `04-Agent-System`, `05-Frontend-UI`

### 3.5 Chapter content format

**Rule 1: file index table columns**

Columns for every file, in order:

| File | Function | Key symbols | Callers | Callees | Tag |

- **File**: path relative to the project root
- **Function**: one-line description of the file's responsibility (extracted from source/docs, never guessed)
- **Key symbols**: the 1-3 most important classes/functions (precise enough to be found by lsp/grep)
- **Callers**: who calls this module (module or file name)
- **Callees**: what this module calls
- **Tag**: 🔴Core / 🟡Aux / ⚠️Stub / 🗑️Deprecated / 📦Config / 🧪Test

**Rule 2: critical paths use numbered steps**

Every critical path is a linear sequence, one step per line, precise down to file and function name:

```
1. Frontend InputArea.vue → gateway.store.sendMessage(text)
2. WebSocket → "prompt.submit" → ws_server.py:handle_ws()
3. Background thread → server.py:handle_prompt_submit(sid, text, write_event)  -- L1269
```

**Rule 3: ASCII topology diagram**

```
[Data layer]     session.py, agent_chat_history.py
               ↑ read/write
[Tool layer]     tools/, extensions/, mcp/
               ↑ call
[Service layer]  agent_chat_service_v2.py (core)
               ↑ call
[Gateway layer]  server.py, ws_server.py
               ↑ call
[Frontend layer] gateway.ts, config.ts
```

Arrow direction is consistent: `↑` means "called by the layer above", `↓` means "calls the layer below".

**Rule 4: boundary note categories**

- `⚠️Stub`: empty body or fixed None return
- `🐛Known issue`: confirmed bug / design defect
- `❗Easy to misread`: the understanding trap an agent is most likely to fall into
- `🗑️Deprecated`: no longer maintained but still present

### 3.6 File index coverage requirements

- Must cover every .py file (excluding __pycache__)
- Must cover every .ts/.vue file (excluding node_modules/dist)
- Must cover every configuration file (.json/.yaml/.toml/.env.example)
- Test files may be reduced to one line (file name + "test")
- Entry files must be described in detail (multiple lines)

### 3.7 Length control

- A single chapter stays under ~1000 lines; split by sub-module when it grows beyond that
- When chapters exceed 15, check whether the granularity is reasonable (merge related systems if too fragmented)
- The v1.x "single file 3000-line cap" is superseded by chaptering and no longer applies

---

## 4. Indexing Mechanism

### 4.1 Tool selection

| Stage | Primary tool | Supporting tools |
|------|--------|---------|
| Full index | mcp__codebase_memory__index_repository | - |
| Architecture extraction | mcp__codebase_memory__get_architecture | list_directory, read_structure |
| File enumeration | glob | list_directory, get_file_info |
| Relationship tracing | mcp__codebase_memory__trace_path | mcp__codebase_memory__search_knowledge base |
| Sampling confirmation | read_file | lsp_document_symbol |
| Generate knowledge base | write_file (per chapter + content.md) | - |

### 4.2 codebase_memory usage notes

- `index_repository` is called once, on the first build (write cost is high)
- `get_architecture` is read-only and can run concurrently with other tools
- `trace_path` is called once per key function; depth=2 is recommended (two levels of callers and callees)
- `search_knowledge base` confirms the existence and location of a specific symbol

### 4.3 Index quality checks

After the build, run these checks:

```
1. File count check: glob results vs the sum of index rows across chapters
   → difference > 5 files → mark as "needs completion"

2. Key symbol check: for the 10 largest files, pick 3 key symbols at random
   → search_knowledge base confirms the symbol exists in the index → fix any mismatch

3. Call relationship check: pick steps from 5 critical paths
   → trace_path verifies the call chain exists → fix any mismatch

4. Entry coverage check: every entry file (a file with a main function) must appear in the file index
```

---

## 5. Verification Logic

### 5.1 Automatic verification checklist

A CKB build must pass the following before it can be marked "complete":

```
□ content.md generated; chapter list matches chapter files (count and names)
□ Every chapter file exists and is non-empty
□ File index covers every file in the glob results (difference < 5)
□ At least one file per directory sampled and confirmed
□ Every entry file has a corresponding step in the critical paths
□ Every file tagged "⚠️Stub" confirmed with read_file (not assumed)
□ Every module in the topology table exists in the file index
□ Every function name in the critical paths is verifiable via search_knowledge base
□ No unresolved "TODO" or "to be confirmed" markers (confirmed ones excepted)
□ VERSION written and consistent with the current file count and index node count
```

### 5.2 Incremental updates

When project code changes:

```
1. Read content.md to locate the affected chapters
2. Read those chapter files and update the affected entries (file index, critical paths, boundary notes)
3. Chapters added/removed: update content.md and rename the affected files per the naming rule (keep indices contiguous)
4. Append a change record to VERSION
```

If the knowledge base clearly disagrees with the actual code (e.g. an indexed file no longer exists), correct it with the same flow.

### 5.3 Format detection

```
- New format (v2.0): knowledgesource/content.md exists
- Legacy format (v1.x): only knowledgesource/PROJECT_GRAPH.md exists
- No knowledge base: neither exists → trigger the build flow
```

---

## 6. Migration (v1.x → v2.0)

### 6.1 Migration steps

```
1. Read the legacy PROJECT_GRAPH.md in full
2. Plan the chapter structure (per 3.4 Chapter division principles)
3. Reorganise the legacy content into chapter files (reorganise, do not rewrite — lose nothing)
4. Generate content.md
5. Delete the legacy PROJECT_GRAPH.md
6. Append a migration record to VERSION
```

### 6.2 Migration principles

- **Lose nothing**: migration is content reorganisation, not regeneration; keep the original wording when in doubt — redundancy is better than omission
- **Entries with unclear ownership**: place them in the most relevant chapter and note the cross-module relationship on the entry
- Run the 5.1 checklist after migration

---

## 7. Integration with the System Prompt

### 7.1 Integration

The CKB mechanism is embedded in agent behaviour in three ways:

1. **Skill file**: `skills/ckb/SKILL.md` contains the condensed detection → build flow; the agent loads it via `load_skill("ckb")`
2. **System prompt injection**: the code_agent system prompt embeds the CKB detection rule (short form)
3. **Skill preload**: the code_agent YAML declares `skills: [ckb]`, ensuring CKB is loaded when the session starts

### 7.2 Rule text in the system prompt

```
## CKB mechanism (mandatory)

At the start of a conversation, probe knowledgesource/content.md:
- Read succeeds → read the directory, locate chapters, read the chapters you need
- Missing → fall back to PROJECT_GRAPH.md (legacy format, suggest migration)
- Neither exists → enter build mode and do nothing else until the knowledge base is generated

Build-mode hard constraints: you may only use read_file, glob, and codebase_memory tools.
Do not write code, do not answer task requests, do not speculate about the project code.
```
