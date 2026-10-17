# 3. Critical Paths

### Path 1: User Message → LLM Response

```
1. InputArea.vue → gateway.store.sendMessage(text)
2. WebSocket "prompt.submit" (token-authenticated: ?token= / X-Pebble-Token; payload limits enforced) → ws_server.py:handle_ws
3. Background thread → server.py:handle_prompt_submit(sid, text, write_event)  --L1269
4. Read llm_providers.json for temperature/top_p/reasoning_effort/thinking_enabled  --L1280
5. service.chat_stream(agent_name, message, ...)  --L1320
6. Inside chat_stream:
   a. get_cached_agent → load YAML agent → agent_loader.py
   b. get_agent_system_prompt → load prompt md
   c. ledger.build_prompt → recalled experience entries + agent note (FTS + tool/path weighting)
   d. usernote + project_path_instruction concatenation
   e. get_agent_chat_messages_for_context → agent_chat.db
   f. _sanitize_messages → repair orphan tool messages
   g. stream_params = {model, messages, temperature, top_p, extra_body: {thinking}}
   h. AsyncOpenAI.chat.completions.create(**stream_params)
   i. while True state machine → parse tool_calls → ToolOrchestrator.run_tools
   j. _calculate_cost + _accumulate_cost → cost
7. yield events → consumed by server.py → write_event → WebSocket
8. gateway.ts → handleEvent → Vue reactive rendering
```

### Path 2: Config Save/Load

```
Save: ModelConfigPanel.vue → config.saveProviders
  → WebSocket "model.provider.save" → server.py → _write_providers(llm_providers.json)

Load: gateway.ts setupConnection → configStore.loadProvidersFromBackend
  → WebSocket "model.options" → server.py → _read_providers
  Note: called on startup
```

### Path 3: WebSocket Event Types (16)

```
message.delta → streaming text
thinking.delta → reasoning text
tool.call_streaming → tool argument stream
tool.start → tool started
tool.complete → tool completed
file.change → file changed
message.complete → message completed
error → error
todos.update → todo list
clarify.request → agent question
permission.request → permission request
message.usage → token usage
user_message.saved → user message saved
session.updated → title updated
gateway.ready → connection ready
prompt.rejected → prompt payload exceeded limits
```

### Path 4: HTML Interactive Comment Loop

```
Agent generates HTML → write_file(project relative path/xxx.html)
  → outputs @pebble-file: xxx.html (plain text, not a code block)
  → clarify(questions=[{prompt, options: [接受, 修改]}])
  → chat_stream suspends waiting for user reply
    │
Frontend: contentParser detects @pebble-file → type='html-file' block
  → MessageContent renders <HtmlPreview :file-path="xxx.html" />
  → HtmlPreview.watch(filePath) → loadFromFile
  → RPC file.read_text(projectPath + filePath) → resolvedContent
  → srcdoc assembled (buildSrcdoc: meta CSP + preview bridge + feedback engine inlined)
  → sandboxed iframe render (sandbox="allow-scripts", opaque origin) + feedback layer activation via postMessage
    │
User: clicks element in iframe → comment popup → save
  → postMessage → comment store
  → AskQuestionCard "Modify" → gateway store attaches comments → clarify.respond
    │
Agent: receives comments → read_file → edit_file(search_block) → file modified
  → clarify again → pendingClarify changes → HtmlPreview re-reads → refresh
  → loop until user clicks "Accept"

Key files: feedbackBridgeContent.ts (inlined CSS/JS), previewBridgeContent.ts (height/capture/theme bridge), comment.ts (Pinia store)
Backend addition: server.py file.read_text method (project-boundary/whitelist guard)
Security note: preview runs sandboxed without allow-same-origin — the host and the preview communicate only via postMessage (source-checked); PNG capture executes inside the iframe and returns a dataURL
Note: the _os variable trap — file.read_text executes before file.read_base64's "import os as _os", so a top-level import os must be used
```

### Debugging Guide: HTML Rendering Issues

If you encounter white/black screens, files not loading, or the feedback layer not activating, enable debug logs in the following files:

| File | Tag | How to enable |
|------|---------|---------|
| `ui-gui-vue3/src/components/HtmlPreview.vue` | `[HtmlPreview]` | Search `// console.log` or `/*console.log*/` and uncomment |
| `ui-gui-vue3/src/stores/gateway.ts` | `[gateway]` | Uncomment `// console.log` near L235 |
| `ui-gui-vue3/src/utils/contentParser.ts` | `[contentParser]` | Uncomment `// console.log` near L179 |
| `gateway/server.py` | `[file.read_text]` | Uncomment `# print(` near L1092-1110 |

Frontend logs appear in the browser DevTools Console; backend logs in the terminal window.
