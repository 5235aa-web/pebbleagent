---
name: 音乐播放
description: 当用户要求打开音乐播放器、搜索歌曲、播放、创建歌单时使用此技能。优先结合 Windows-Use（GUI 操作）和 Open Interpreter（代码执行）。
version: 1.0
tags: [music, desktop, gui, windows]
---

# 音乐播放

# 音乐播放控制技能

## 使用时机
- 用户说“打开音乐播放器“、“播放音乐”、创建歌单”等。
- 任务涉及桌面音乐 App 的打开、搜索、点击、播放等操作。

## 推荐步骤（严格按此顺序思考和执行）
1. **检查当前状态**：使用 Windows-Use 工具查看是否有音乐播放器窗口已打开。
2. **启动应用**（如果未打开）：使用 Open Interpreter 执行启动命令（如 subprocess.run(["start", "网易云音乐"])）。
3. **界面操作**：使用 Windows-Use 进行 GUI 控制（点击搜索框、输入歌曲名、点击播放按钮）。
4. **歌单处理**：如果需要创建歌单，使用 Open Interpreter 读写文件（playlist.txt）。
5. **确认结果**：操作完成后，用工具确认当前正在播放什么，并反馈给用户。
6. **错误处理**：如果播放器未响应 → 重启；搜索无结果 → 尝试相似歌曲或本地文件。

## 最佳实践
- 优先 GUI 控制（Windows-Use），因为更接近真人操作。
- 文件相关操作交给 Open Interpreter。
- 每次行动后必须观察结果，如果失败则重新规划。
- 保持用户友好：操作前/后可以简单告知用户正在做什么。

## 示例
**用户输入**：播放GRACIE ABRAMS的mess it up  
**Agent 思考**：需要 music_control 技能 → 检查窗口 → 启动网易云 → 输入“稻香” → 点击第一首播放 → 确认。

**用户输入**：帮我创建一个“放松”歌单  
**Agent 思考**：使用 music_control + file_management 技能 → 创建 txt 文件 → 添加歌曲。