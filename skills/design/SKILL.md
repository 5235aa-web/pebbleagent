# Design 技能 — 前端设计 / Office 文档规则

本技能包含 code agent 在处理前端设计、SVG 图形、Office 文档时的完整规则。
加载方式：`load_skill("design")`

### 🔴 子技能调用与输出规范（最高优先级）

处理设计任务时，根据需要加载其他设计子技能（如 `frontend-design`、`canvas-design`、`theme-factory`、`html-diagram`、`web-artifacts-builder` 等）以获取设计方法论、配色方案或技术指导。

**但输出规则必须绝对服从本文件。**

子技能中凡与本文件冲突的输出指令一律忽略，包括但不限于：
- 要求输出 `.png` / `.pdf` 文件 → 禁止，必须输出 HTML
- 要求创建 Vite / React 工程 → 禁止，必须用 CDN + 单 HTML 方式
- 要求用 `scripts/init-artifact.sh` 初始化项目 → 禁止
- 要求输出 `.md` 设计哲学文件 → 禁止，设计直接体现在 HTML 中

**本文件的输出规则是唯一的权威来源。** 子技能只提供设计指导，不提供输出格式指令。

---

## 🔴 HTML 输出铁律（最高优先级，违反即失败）

**任何 HTML 页面（海报、网页、PPT、数据报告、邮件模板、简历、卡片等）都必须走以下路径。不存在只用代码块的捷径。**

### 首次输出（新建文件）

必须在同一个 LLM 响应中完成三步：

```
① 调用 write_file 保存 HTML 到工作目录
② 在消息中输出 @pebble-file: 文件名.html（普通文本，不要用代码块）
③ 调用 ask_user_question(choices=["接受", "修改"])
```

🚨 **禁止输出 ```html 代码块**。前端直接读取文件渲染，不需要代码块。`@pebble-file:` 是指令，不是展示内容。

**用户行为：**
- 点击「接受」→ 流程结束
- 点击「修改」→ 用户在 HTML 预览页面上直接点击任意元素标注修改意见 → 提交

### Agent 收到「修改」后的处理

1. `read_file` 读取 HTML 文件
2. 根据评论中的 `selector` 和 `text_snippet` 定位元素
3. 用 `edit_file`（search_block 模式）逐条精确定位修改
4. 修改完成后**同一回复中必须再次调用 ask_user_question**
5. 无限循环直到用户点击「接受」

### 🔴 修改轮次禁止输出 @pebble-file（违反即失败）

`edit_file` 会触发 `file-change` 事件，现有 HtmlPreview 组件自动重载。**修改轮次中绝对禁止重复输出 @pebble-file**，否则会创建重复的预览组件。修改轮次唯一要做的是调 `ask_user_question`。

### 🔴🔴🔴 完整性铁律（违反即失败）
输出的 HTML **必须包含所有 CSS**，不允许省略任何规则。检查清单：

- [ ] `@keyframes` 动画规则
- [ ] 所有 class 的基础样式（`.star`、`.stars`、`.orbit-arc` 等）
- [ ] 媒体查询内的覆盖样式
- [ ] **自检方法**：复制你输出的 `<style>` 块，去掉所有 `@media` 后看是否仍有完整的、可独立渲染的样式。如果去掉媒体查询后样式不完整 = 漏了基础 CSS = **重写**

**典型错误（禁止）：**
```
<style>
@keyframes twinkle { ... }         ← 只有动画和媒体查询
@media (max-width: 860px) { ... }  ← 没有基础样式
</style>
<!-- ❌ 缺少 .star { background: #fff; border-radius: 50%; } 等基础规则 -->
```

**正确做法：**
```
<style>
/* 1. 基础样式 */
.star { position: absolute; background: #fff; border-radius: 50%; }
.star-s { width: 1px; height: 1px; }

/* 2. 动画 */
@keyframes twinkle { ... }

/* 3. 媒体查询 */
@media (max-width: 860px) { ... }
</style>
```

完成后如果用户需要保存，他们可以点击预览卡片的保存按钮将其写入工作区。

### 🔴 防 AI 味自检清单（输出前逐条过）

生成 HTML 后必须自查以下 20 条，任意一条为"是"→ 修改后再输出。

**字体与色彩：**
1. 是否用了 Inter / Roboto / Poppins / Open Sans / Lato？→ 换小众字体
2. 是否出现紫色→蓝色渐变（含 `background-clip: text` 渐变文字）？→ 禁止
3. 是否用了纯 `#000` 或纯 `#fff` 作为大面积底色？→ 用带色相的深色/浅色替代
4. 中性灰（`#888`、`#999`、`#ccc`）是否没有向主题色偏色？→ 所有灰色加点主题色相（oklch 中 chroma ≥ 0.005）

**布局与结构：**
5. Hero 区是否 `min-height: 100vh` 且全部内容居中堆叠？→ 至少让 eyebrow 或 CTA 偏离中轴
6. 是否出现三列等宽 icon+标题+描述 卡片网格？→ 换不规则布局
7. 卡片是否嵌套在另一个卡片内？→ 禁止
8. 是否每个 section 之间只有等距空白，无分割线/色块/装饰打破节奏？→ 增加节奏变化

**CSS 细节：**
9. 是否用了 `transition: all`？→ 明确指定过渡属性
10. 是否多处元素统一 `hover:scale(1.05)`？→ 每类元素用不同 hover 效果
11. 是否在 UI 交互（按钮/弹窗/tooltip）上用了弹性缓动（`cubic-bezier` overshoot）？→ 保留给物理交互
12. 是否在动画 `width`/`height`/`top`/`left`？→ 用 `transform` 替代
13. 任意 padding/gap/margin 是否不是 4 的倍数？→ 统一到 `--space` 系列（4px 步进）
14. 是否缺 `:focus-visible` 样式？→ 必须有，且 focus ring 用 `outline` 不用 `border`

**内容与图标：**
15. 是否用 ✨🚀⚡🔥🎯✅ 等 emoji 做 feature card 图标？→ 用 SVG 图标库（Lucide）或去掉
16. 是否混用两个以上图标库？→ 统一一个
17. 是否手绘了假的浏览器窗口框/手机框/终端框？→ 禁止

**动效与可访问性：**
18. 是否动画没包 `@media (prefers-reduced-motion: reduce)` 回退？→ 必须包
19. 按钮/链接文字是否在 320px 宽度下折成两行？→ `white-space: nowrap` 或缩短文案
20. 页面 320px–1920px 之间是否出现横向滚动条？→ `html, body { overflow-x: clip }`

---

## SVG 图形生成指南

当用户要求生成**图标、徽章、数据可视化图形、流程图、架构图、插图**等矢量图形时，使用 ` ```svg ` 代码块输出。

原因：前端会自动将 ` ```svg ` 代码块渲染为 SVG 实时预览卡片，用户可以直接看到效果，支持下载为 `.svg` 文件、复制代码、保存到工作区。

**正确做法：**
~~~~
```svg
<svg viewBox="0 0 200 100" xmlns="http://www.w3.org/2000/svg">
  ...你的SVG代码...
</svg>
```
~~~~

**要点：**
- SVG 必须包含 `xmlns="http://www.w3.org/2000/svg"` 属性
- 使用 `viewBox` 确保缩放正确，不要用固定 `width`/`height`
- 颜色、样式用内联属性或 `<style>` 标签写在 SVG 内部
- 复杂图形用 `<g>` 分组组织

---

## 写作流程：选方向

分清楚用户要的是"视觉展示"还是"前端代码"，然后选对应的技能加载：

**① 视觉展示类** — 用户要的是"看"的效果（海报、演示幻灯片、动画、信息图、交互 Demo）。输出是展示性 HTML，不需要交互逻辑和后端。
- **普通模板**（75 个 html-anything）：直接输出，适合快速出图
- **🔴 要高质量的视觉** → `load_skill("huashu-design")` — 品牌资产搜索、设计方向选择、React 运行时、动画引擎、Playwright 验证。适合用户说"做个好看的发布会 PPT / 产品原型 / 品牌动画 / App Demo"
- `load_skill("canvas-design")` — 创建博物馆级视觉艺术品/海报/设计作品（.png/.pdf）。含完整设计哲学体系（设计运动宣言 → 视觉表达 → 精雕细琢），强调工匠感与审美独特性，适合纯视觉输出
- `load_skill("html-diagram")` — SVG 架构图/系统图，全屏交互式，自动暗色模式。适合做技术栈图、系统架构图、调用链路图等（参考示例见 `skills/html-diagram/references/`）

**② 前端代码类** — 用户要的是"能用"的界面（网站、App 页面、产品前台、Dashboard）。输出是生产级代码，关心布局、交互、响应式。
- `load_skill("taste-skill")` — 通用高端前端，安全默认，适合大多数场景
- `load_skill("minimalist-skill")` — Notion/Linear 极简风
- `load_skill("soft-skill")` — 柔和高质感，大留白
- `load_skill("brutalist-skill")` — 工业风实验性布局
- `load_skill("redesign-skill")` — 改造现有项目 UI
- `load_skill("frontend-design")` — **Anthropic 官方前端 UI 设计技能**。完整的设计指南（排版/色彩/动效/空间构图），强调避免 AI 审美同质化（拒绝紫色渐变/Inter 字体等套路），产出有鲜明视觉个性的生产级前端界面
- `load_skill("web-artifacts-builder")` — **Anthropic 官方复杂前端项目构建工具**。React + TypeScript + Vite + Tailwind CSS + shadcn/ui 技术栈，含项目初始化和单 HTML 打包脚本，适合需要状态管理/路由的复杂前端 Artifact
- `load_skill("theme-factory")` — **Anthropic 官方主题工厂**。10 套预设配色+字体主题（Ocean Depths / Sunset Boulevard / Modern Minimalist 等），可快速应用到 HTML 页面/文档/幻灯片，也支持按需生成自定义主题

**写代码**：使用 `write_file` 创建 `.html` / `.vue` / `.tsx` 等文件

**🔴🔴🔴 前端项目必须用 CDN + 单 HTML 方式预览（禁止创建 Vite 项目，强制规则）**
- 当用户要求写 **Vue / React / 任何前端代码** 时，**禁止**创建 Vite / CRA / Next.js 等需要 `npm install` + `npm run dev` 的多文件工程
- 原因：用户在聊天界面里等你交付，不是等你去跑脚手架。你的最终输出是一段代码，不是一堆工程文件
- **唯一正确做法**：
  ① 用 `write_file` 创建实际的组件文件（`.vue` / `.tsx` 等）
  ② **再生成一个独立的 `preview.html`**，通过 CDN `<script>` 引入 Vue/React 运行时，挂载并渲染组件
  ③ 以 `@pebble-file: preview.html` 输出到回复中 → 前端实时预览
- 预览 HTML 文件**写在工作目录下**（`write_file` 默认路径），不要写临时目录
- **禁止**说"需要 npm install 才能看到效果"——用户要的是在聊天里直接看，不是去开终端

**导出**：用户可一键将 HTML 内的 `<style>` 样式内联到元素上，适合直接粘贴到微信/知乎等平台

---

## 补充：html-diagram — SVG 架构图技能
当需要生成**架构图、系统图、技术栈图**等 SVG 图表时，可加载内置的 `html-diagram` 技能：
- **路径**：`E:\pebbleagent\skills\html-diagram\`
- **加载方式**：`load_skill("html-diagram")`
- **特点**：全屏 SVG 优先，交互式节点，自动暗色模式（CSS 变量 + theme toggle + localStorage 持久化）
- **参考示例**：见 `skills/html-diagram/references/html-effectiveness/` 目录（20 个高质量 HTML 示例覆盖代码探索、原型动画、流程图、SVG 插图等场景）
- **最佳实践**：SVG 样式通过 CSS 类使用主题变量，禁止硬编码 hex 值，确保暗色模式自动跟随

---

## Office 文档任务 — 使用 OfficeCLI（⚠️ 强制规则）

当用户要求创建/编辑/分析 **Word 文档 (.docx)、Excel 表格 (.xlsx)、PowerPoint 演示 (.pptx)** 时，**必须使用 `officecli` 工具**。

**🔴🔴🔴 禁止叛逃到 python-pptx / python-docx / openpyxl（违反即失败）：**
即使 officecli 遇到困难（超时、schema 验证失败、参数记不住），也**绝对禁止**放弃 officecli 改用 Python 库。
- ❌ 禁止用 `python-pptx` 替代 officecli 创建/编辑 PPTX
- ❌ 禁止用 `python-docx` 替代 officecli 创建/编辑 DOCX
- ❌ 禁止用 `openpyxl` 替代 officecli 创建/编辑 XLSX
- ❌ 禁止用 `zipfile` 直接操作 OOXML 压缩包

遇到困难时正确的做法：
1. `officecli help pptx` — 查帮助，看正确语法
2. `load_skill("officecli-pptx")` — 重新加载子技能获取设计规范
3. 如果某个属性报错，换另一种方式实现（如 `--prop background=HEX` 不行就 `--prop "background=HEX-HEX-180"`）
4. 实在不行就**向用户报告具体问题和尝试过的方案**，而不是自作主张换工具

### 工作流程
1. **选技能**：`list_skills` 查看可用 officecli 子技能，用 `load_skill` 加载对应技能
2. **查帮助**：不确定属性名或取值格式时，先查帮助 `officecli help <format> <element>`，禁止靠猜
3. **创建文件**：`officecli create <file>` 创建新文件
4. **增量构建**：L1(只读/view) → L2(DOM操作) → L3(原始XML，兜底)。加 `--json` 获取结构化输出
5. **🔴🔴🔴 视觉预览（三步不能少，必须严格执行）：**
   ① `powershell` → `officecli view <file> html -o E:\workspace\preview.html`（**必须用绝对路径**，否则文件会写到临时目录）
   ② `read_file E:\workspace\preview.html` → **必须读取文件内容，禁止跳过此步**
   ③ 以 ` ```html ` 代码块输出到回复中

   **违反后果：** 如果不执行第②步，用户看不到任何预览，等于白做。
   **🔴 绝对禁止：**
   - ❌ 禁止以"文件太大"为由跳过读取。HTML 预览文件不大，读完整内容
   - ❌ 禁止自己"模拟"一个预览 HTML 来替代真实输出
   - ❌ 禁止任何形式的"打开浏览器"操作（`Start-Process`、`explorer`、`open`、`Invoke-Item` 等）
   - ✅ **唯一正确的方式：** 读取真实预览文件 → ` ```html ` 代码块输出
6. **根据反馈迭代**：用户看了预览说改 → 修改 → 重复第 5 步 → 直到满意
7. **最终验证**：`officecli validate <file>` + `officecli view <file> issues`

### 🔴🔴🔴 所有 officecli 命令必须用 powershell 工具执行
原因：`execute_shell` 走 cmd.exe，会错误解析 `--prop` 参数中的特殊字符（`=`, `,`, `.`, `(`等），导致 `bold`、`size`、`color`、`fill`、`background`、`font` 等样式属性**静默丢失**。最终文档只有纯文字。

**允许/禁止的工具：**
- ✅ `powershell` → 所有 `officecli` 命令（保留样式）
- ❌ `execute_shell` → cmd 转义丢样式
- ❌ `execute_code`（Python subprocess）→ 同样走 cmd，同样丢样式
- ✅ `read_file` → 读取预览 `.html`

**🔴 每次 powershell 执行 officecli 前必须先设置 PATH：**
```powershell
$env:Path = "$env:LOCALAPPDATA\OfficeCLI;$env:Path"; officecli ...
```
officecli 安装在 `%LOCALAPPDATA%\OfficeCLI\`，PATH 更新在当前终端会话中不会生效，必须手动设置。

```powershell
# ✅ 正确 — 先设置 PATH 再执行
powershell: $env:Path = "$env:LOCALAPPDATA\OfficeCLI;$env:Path"; officecli add deck.pptx '/slide[1]' --type shape --prop text="标题" --prop bold=true --prop size=36 --prop color=FFFFFF

# ❌ 禁止 — execute_shell 静默丢弃 bold/size/color，变成纯文字
execute_shell: officecli add deck.pptx '/slide[1]' --type shape --prop text="标题" --prop bold=true --prop size=36 --prop color=FFFFFF
```

### 🔴🔴🔴 PPT 质量铁律（违反即失败）
制作 PowerPoint 时，**绝对禁止**偷工减料。
- ❌ 禁止因觉得 `background` 属性太复杂就放弃渐变背景。`--prop background=COLOR1-COLOR2-180` 即可，不复杂
- ❌ 禁止因 `--type chart` 参数太多就放弃图表，改用纯文字描述数据
- ❌ 禁止只放文字不配图/形状/图标。**每页幻灯片必须有非文字视觉元素**（形状、图表、图标、渐变带、图片等）
- ❌ 禁止使用默认白色背景不做任何设计
- ❌ 禁止字体小于规范值（标题 ≥ 36pt、正文 ≥ 18pt）
- ✅ `load_skill("officecli-pptx")` 后严格按照子技能中的设计规范执行（配色、字号、网格、布局模式）
- ✅ 使用 `--prop background=HEX` 或 `--prop background=COLOR1-COLOR2-180` 设置渐变背景
- ✅ 使用 `--type chart` 添加图表，`--type picture` 添加图片，`--type shape` 添加装饰形状
- ✅ 先 `officecli view <file> html -o preview.html` 看效果，不满意就改，直到美观为止

### 基础命令速查
```powershell
officecli create report.docx                          # 创建文档
officecli add report.docx /body --type paragraph --prop text="内容" --prop style=Heading1
officecli set report.docx '/body/p[1]/r[1]' --prop bold=true  # 修改属性
officecli get report.docx '/body/p[3]' --depth 2 --json     # 读取元素
officecli query report.docx 'paragraph[style=Heading1]'     # CSS风格查询
officecli view report.docx outline                          # 查看结构
officecli view report.docx html -o preview.html       # 生成HTML预览文件
officecli batch data.xlsx --commands '[...]' --json         # 批量操作
officecli close report.docx                                 # 保存关闭
```

### 子技能体系
专门的文档类型需要加载对应的子技能（融资路演 PPT 用 `officecli-pitch-deck`，学术论文用 `officecli-academic-paper`，金融模型用 `officecli-financial-model` 等）。每个子技能包含该领域的设计约束和最佳实践。

---

## Frontend Design Rules (from GPT-5.5 Codex & Claude Design)

These rules apply when building HTML pages, web apps, or any visual UI. They are mandatory.

### Controls & UI Elements

- Prefer icons inside buttons for tools, swatches for color, segmented controls for modes, toggles/checkboxes for binary settings, sliders/steppers/inputs for numeric values, menus for option sets, tabs for views. Use text or icon+text buttons only for clear commands.
- Use Lucide icons whenever one exists instead of manually-drawn SVG icons. If a library is available, use its icons.
- Build tooltips that name/describe unfamiliar icons on hover.
- Cards: border-radius ≤ 8px unless the existing design system requires otherwise.

### Layout & Spacing

- **No card-in-card.** Do not put UI cards inside other cards. Do not style page sections as floating cards. Cards are only for repeated items, modals, and genuinely framed tools. Page sections must be full-width bands or unframed layouts with constrained inner content.
- **No discrete orbs, gradient orbs, or bokeh blobs** as decoration or backgrounds.
- Match display text to its container: reserve hero-scale type for true heroes; use smaller, tighter headings inside compact panels, cards, sidebars, dashboards, and tool surfaces.
- Define stable dimensions with responsive constraints (aspect-ratio, grid tracks, min/max, container-relative sizing) for fixed-format UI elements so hover states, labels, icons, or dynamic content cannot resize or shift the layout.

### Typography

- Text must fit within its parent UI element on all viewports. Move to new line if needed; use dynamic sizing so the longest word fits. Text must not occlude preceding or subsequent content.
- Do not scale font size with viewport width.
- Letter spacing must be 0 (not negative).

### Color

- Avoid one-note palettes: do not let the UI be dominated by variations of a single hue family. Limit dominant purple/purple-blue gradients, beige/cream/sand/tan, dark blue/slate, and brown/orange/espresso palettes. Scan CSS colors before finalizing; revise if the page reads as one of these themes.
- Use oklch to define harmonious colors that match the existing palette when the design system's palette is too restrictive. Avoid inventing new colors from scratch.
- Build feature-complete controls, states, and views that a target user would naturally expect.

### Editing Existing UI

- Understand its visual vocabulary first: copywriting style, color palette, tone, hover/click states, animation styles, shadow + card + layout patterns, density.
- When the user asks for a small, targeted change — some text, a color, one element — change ONLY that. Leave all other layout, spacing, margins, fonts, sizes, positions, colors, and content exactly as they are. Do not redesign or "improve" parts you weren't asked to touch.
- Avoid web design tropes and conventions unless you are making a web page.

### Hero / Landing Pages

- Hero content must leave a hint of the next section's content visible on every viewport.
- When making a hero, use a relevant image or immersive scene as background with text over it (not in a card). Never use a split text/media layout where a card is one side and text is on another. Never put hero text in a card. Never use a gradient/SVG hero illustration when a real or generated image can carry the subject.
- Do not make a landing page unless absolutely required; when asked for a site, app, game, or tool, build the actual usable experience as the first screen.
- Do not use visible, in-app text to describe the application's features, functionality, keyboard shortcuts, styling, visual elements, or how to use the application.
