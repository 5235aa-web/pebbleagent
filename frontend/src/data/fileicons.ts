/* File-kind glyphs for the project tree.

   One stroke colour and no fill: the kind is carried by the shape alone, so the
   tree needs no per-kind palette and both themes keep working untouched. The
   markup is what the rest of the app emits by hand (`glyph()` in
   CommandPalette.vue, `toolIcon()` in render.ts): a 24x24 viewBox, no fill, a
   currentColor stroke, so a glyph inherits the colour of the row holding it.

   Nothing here is built from the file name. The name only selects a constant
   string, which is what makes the result safe to place with `v-html`. */

export type FileKind =
  | 'dir' | 'code' | 'data' | 'doc' | 'image' | 'style' | 'archive' | 'binary' | 'plain'

const GLYPHS: Record<FileKind, string> = {
  dir: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  code: '<polyline points="8 6 3 12 8 18"/><polyline points="16 6 21 12 16 18"/>',
  data: '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1"/>'
    + '<path d="M16 3h1a2 2 0 0 1 2 2v5a2 2 0 0 0 2 2 2 2 0 0 0-2 2v5a2 2 0 0 1-2 2h-1"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
    + '<polyline points="14 3 14 8 19 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/>'
    + '<polyline points="21 15 16 10 5 21"/>',
  style: '<line x1="9" y1="3" x2="7" y2="21"/><line x1="17" y1="3" x2="15" y2="21"/>'
    + '<line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/>',
  archive: '<path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5"/><line x1="12" y1="13" x2="12" y2="21"/>',
  binary: '<rect x="7" y="7" width="10" height="10" rx="1"/><line x1="12" y1="3" x2="12" y2="7"/>'
    + '<line x1="12" y1="17" x2="12" y2="21"/><line x1="3" y1="12" x2="7" y2="12"/><line x1="17" y1="12" x2="21" y2="12"/>',
  plain: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'
}

const KIND_BY_EXT: Record<string, FileKind> = {
  // source
  ts: 'code', tsx: 'code', js: 'code', jsx: 'code', mjs: 'code', cjs: 'code',
  py: 'code', pyi: 'code', vue: 'code', svelte: 'code', rs: 'code', go: 'code',
  java: 'code', kt: 'code', kts: 'code', scala: 'code', groovy: 'code',
  c: 'code', h: 'code', cpp: 'code', cc: 'code', cxx: 'code', hpp: 'code', hh: 'code',
  cs: 'code', rb: 'code', php: 'code', swift: 'code', dart: 'code', lua: 'code',
  r: 'code', pl: 'code', jl: 'code', ex: 'code', exs: 'code', erl: 'code',
  hs: 'code', ml: 'code', fs: 'code', clj: 'code', asm: 'code', s: 'code',
  sh: 'code', bash: 'code', zsh: 'code', fish: 'code', bat: 'code', cmd: 'code',
  ps1: 'code', psm1: 'code', sql: 'code', graphql: 'code', gql: 'code', proto: 'code',
  html: 'code', htm: 'code', xhtml: 'code', gradle: 'code', cmake: 'code',

  // structured data and configuration
  json: 'data', jsonc: 'data', json5: 'data', yaml: 'data', yml: 'data',
  toml: 'data', ini: 'data', cfg: 'data', conf: 'data', properties: 'data',
  env: 'data', csv: 'data', tsv: 'data', xml: 'data', plist: 'data', lock: 'data',
  ipynb: 'data',

  // prose
  md: 'doc', markdown: 'doc', mdx: 'doc', txt: 'doc', rst: 'doc', adoc: 'doc',
  org: 'doc', tex: 'doc', pdf: 'doc', doc: 'doc', docx: 'doc', rtf: 'doc', log: 'doc',

  // images
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image',
  svg: 'image', bmp: 'image', ico: 'image', tif: 'image', tiff: 'image',
  avif: 'image', heic: 'image', psd: 'image', ai: 'image',

  // stylesheets
  css: 'style', scss: 'style', sass: 'style', less: 'style', styl: 'style',

  // packages and archives
  zip: 'archive', rar: 'archive', '7z': 'archive', tar: 'archive', gz: 'archive',
  tgz: 'archive', bz2: 'archive', xz: 'archive', zst: 'archive',
  jar: 'archive', war: 'archive', whl: 'archive',

  // compiled and opaque binaries
  exe: 'binary', dll: 'binary', so: 'binary', dylib: 'binary', bin: 'binary',
  obj: 'binary', o: 'binary', lib: 'binary', a: 'binary', class: 'binary',
  pyc: 'binary', pyo: 'binary', wasm: 'binary', node: 'binary', pdb: 'binary',
  msi: 'binary', dmg: 'binary', deb: 'binary', rpm: 'binary', appimage: 'binary',
  ttf: 'binary', otf: 'binary', woff: 'binary', woff2: 'binary'
}

/* Names whose extension either lies (CMakeLists.txt is build script, not prose)
   or is missing entirely. */
const KIND_BY_NAME: Record<string, FileKind> = {
  dockerfile: 'data', '.dockerignore': 'data', procfile: 'data',
  makefile: 'code', gnumakefile: 'code', 'cmakelists.txt': 'code',
  license: 'doc', licence: 'doc', copying: 'doc', notice: 'doc', authors: 'doc',
  readme: 'doc', changelog: 'doc', contributing: 'doc',
  '.gitignore': 'data', '.gitattributes': 'data', '.gitmodules': 'data',
  '.editorconfig': 'data', '.npmrc': 'data', '.nvmrc': 'data', '.yarnrc': 'data',
  '.babelrc': 'data', '.eslintrc': 'data', '.prettierrc': 'data', '.env': 'data'
}

/** Extension of a name, lowercased, empty when there is none. A leading dot is a
    hidden file rather than an extension: `.gitignore` has none. */
function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** `.env.local` and `.eslintrc.json` are configuration, yet neither full name is
    in the table and `local` is not an extension. Read the first segment instead. */
function hiddenHead(lower: string): FileKind | undefined {
  if (!lower.startsWith('.')) return undefined
  const head = lower.indexOf('.', 1)
  return KIND_BY_NAME[head > 0 ? lower.slice(0, head) : lower]
}

export function fileKind(name: string, type: string): FileKind {
  if (type === 'directory') return 'dir'
  const lower = name.toLowerCase()
  return KIND_BY_NAME[lower] ?? hiddenHead(lower) ?? KIND_BY_EXT[extOf(lower)] ?? 'plain'
}

/** Glyph markup for one tree entry. `size` should match the slot the CSS
    reserves, otherwise the stroke is rescaled and the icons stop matching. */
export function fileGlyph(name: string, type: string, size = 11): string {
  const body = GLYPHS[fileKind(name, type)]
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"`
    + ` stroke-width="2" aria-hidden="true">${body}</svg>`
}
