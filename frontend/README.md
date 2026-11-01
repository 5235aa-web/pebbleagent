# Pebble Orbit — new frontend

A one-to-one reconstruction of `design-preview/pebble-orbit.html`.
Visual and behavioural parity is the only acceptance criterion so far; backend
wiring comes later.

## Run

```
npm install
npm run dev
```

Port 5180. Compare against the prototype at 1600x980 and 1280x900.

## Why it is built this way

The prototype is 9539 lines: 190 KB of hand-written CSS, 76 KB of imperative
JavaScript, no framework. Reproducing it pixel for pixel means keeping the CSS
and the DOM exactly as they are and changing nothing about how they compose.

- `styles/orbit.css` is the prototype stylesheet extracted verbatim, loaded
  globally. No `scoped` blocks anywhere: scoped rewrites every selector with a
  `[data-v-*]` suffix, which raises specificity one notch and silently changes
  which rules win.
- `styles/fonts.css` loads JetBrains Mono on its variable axis (100-800). The
  prototype uses weights 550 and 650, which only exist on `wght`.
- `vite.config.ts` sets `whitespace: 'preserve'`. The prototype relies on
  newline-generated gaps between inline elements; Vue's default `condense`
  would delete those text nodes and shift layout.
- Components are slices of the original markup: same tags, same classes, same
  order. Splitting therefore cannot change rendering.

## Layout

```
src/
  main.ts               mounts App, then starts the interaction runtime
  App.vue               shell only: backdrop, app frame, overlay registry
  styles/
    fonts.css
    orbit.css
  assets/fonts/
  orbit/
    runtime.js          prototype script, wrapped as initOrbit()
  components/
    AmbientBackdrop.vue
    OrbitalBar.vue        -> BrandMark, ProjectSwitcher, SessionSwitcher,
                             IslandWrap, BarActions
    StreamPane.vue        -> StreamHead, MessageStream, StreamTimeline,
                             PlanCard, ComposerWrap
    RightDock.vue         -> DockHead, FilesPane, UsagePane, ContextPane,
                             LlmPane, NotesPane
    StatusBar.vue
    PreviewStage.vue
    CommandPalette.vue
    ModelPanel.vue
    LedgerPanel.vue
    NewEntryDialog.vue
    McpPanel.vue
    PairingPanel.vue
    ThemeSheet.vue
    NotifySheet.vue
    PathsPanel.vue
    ToolsPanel.vue
    PermissionGate.vue
    ImageViewer.vue
    DropOverlay.vue
    ConversationMenu.vue
    MessageSearch.vue
    ToastStack.vue
```

## Interaction layer

`initOrbit()` runs after `mount('#app')`. It is the prototype's script, wrapped
rather than rewritten, so every binding, animation trigger and dynamic element
behaves exactly as in the prototype.

This works because the Vue tree currently holds no reactive state: it renders
once at mount and never patches again, so the imperative code and Vue never
write to the same node. That invariant has to hold until the runtime is
refactored into per-component modules; when a component starts owning reactive
state, its slice of the runtime must move into that component.

## Verification

Every structural change is checked by expanding the component tree back into a
static page and comparing it against the prototype in the same viewport:

- element counts (total, div, span, button, svg)
- bounding boxes of the frame, stream, dock, composer, message, bubble,
  plan card, dock pane
- document scroll size (no horizontal overflow)
- console clean

## Data layer

`src/data/` holds everything the prototype hard-coded in markup:

- `store.ts` — sessions (groups, active id), the plan and its steps, the
  permission queue with each row's settled / verdict / leaving flags.
- `messageMock.ts` — the seven stream blocks lifted verbatim out of the
  prototype template. `stream.ts` owns that array and the streaming flag.
- `gateway.ts` — JSON-RPC over WebSocket, token resolution (Tauri command first,
  dev injection second), reconnect backoff, request timeouts.
- `render.ts` — markdown (paragraphs, headings, lists, inline code, emphasis,
  links), fenced code blocks with a gutter and tk-* tokens, the three tool card
  states, thinking rails, user bubbles and attachment chips.
- `wire.ts` — maps the gateway's push events onto the stores, renders live rows,
  builds the transcript from `session.resume`, and owns prompt submit,
  interrupt and permission decisions.

Components render from these with `v-for` and bindings; the runtime only mutates
state. Anything that used to be "open by default" is a field now (`plan.open`,
`permissions.open`) rather than a class baked into the markup.

Because these lists are reactive, the rule above narrows: the runtime may still
query and animate nodes, but it must not own the class that represents state.
Rows are re-created on change, so every stream handler (tool cards, summary
fold, decision options, copy buttons) is delegated on `document`.

## Fonts

`styles/fonts.css` is deliberately not imported. The prototype names
"JetBrains Mono" first in `--font-mono` but never ships the file, so it renders
with the Cascadia Code fallback. Loading the face shifts every monospace metric
(a model chip reads 108.88px instead of 106.72px) and drifts each agent message
by about 1px. A desktop build that wants a deterministic font should import it
and re-measure the whole surface.

## Next

1. Put the llm and notes panes on their RPCs; they still show prototype values.
2. Empty, loading and error states for the stream and the panes.
3. Move toast, overlays and the command palette onto store state.
