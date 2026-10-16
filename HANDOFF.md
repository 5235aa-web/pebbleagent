# HANDOFF — 2026-10-17 安全评审修复轮

> 本轮针对开源反馈（安全评审）的问题 0-8 完成全量修复 + 1 个衍生发现（exe 遗留 bug 排查）。
> 详细核实结论与修复说明见本轮对话；代码级索引见 `knowledgesource/`（Ch 04/05/06 + VERSION）。

## 任务概述

**为什么做**：开源仓库收到一份长安全评审反馈（工具链断链、任意路径写入、命令校验不一致、权限提权面、审计缺陷、回滚覆盖、编排层问题）。经逐条核实（含快照时间线取证、运行时日志分析），反馈基本属实。用户指示：全部修复。

**最重要的一个修复（问题 0）**：2026-10-16 的"删除状态机"操作删掉了 `ToolExecutionContext.state` 字段，但 `tool_orchestrator.py` 里 6 处 `context.state.turn_count` 引用漏删——**源码版工具链已断**（并行路径静默吞异常、串行路径整轮报错）。当时未暴露是因为桌面版跑的是 17:03 打包的旧 exe。修复：删除 6 处引用（`iteration` 为只写不读死字段）+ 新增 e2e 回归测试守护。

## 关键决策

| 决策 | 理由 |
|------|------|
| 问题0：删 6 处引用（而非恢复 state 字段） | `iteration` 字段全仓库零消费方；恢复状态机字段是倒退 |
| 问题1：filename 净化 + PathBoundaryChecker 双保险 | 净化给清晰错误、边界校验兜底（与 file.* RPC guard 同模式） |
| 问题2：新建 PowerShellValidator + code_destructive_guard | 独立模块、与 BashValidator 同级；bash 分支直接复用 BashValidator |
| 问题4：复用 tool_permission 单一映射源 + 未映射默认 UNKNOWN_TOOL | 消除两套映射不一致；信任键与 exec:command 隔离 |
| 问题6：快照加 write_result_sha256（写后回填）+ 文件级 known-set 判定 | 无法区分"AI 状态/外部修改"的根因是缺"写后状态"记录；旧数据无回填时保持兼容 |
| 问题8：仅做安全提取（_build_and_sanitize_tool_calls），chat_stream 整体拆分留专项 | 1115 行整体拆分风险高；纯逻辑段提取已验证 |
| 验证策略：每个问题配独立反馈回路测试 + 静态守护断言 | 直接回应"缺端到端集成测试"的批评；防断链回归 |

## 文件变更清单

**修改（16 个）**：
- `agent_core/tool_orchestrator.py`（问题0 删引用；问题8 执行器合并 _execute_tool_core）
- `agent_core/query_state_machine.py`（问题8 死代码清理：ChatMessage/MessageType/6 事件类/parse_tool_calls_from_response）
- `agent_core/tool_executor_impl.py`（问题1 净化；问题2 接入；问题6 回填钩子；7e copy_file 快照）
- `agent_core/permission_enforcer.py`（问题5 log_permission_check 接入）
- `agent_core/agent_chat_service_v2.py`（问题8：token 统一/死方法删除/sanitize 非原地/提取/print 清理）
- `agent_core/context_compressor.py`（问题8 token 统一：estimate_tokens 唯一口径）
- `agent_core/snapshot/snapshot_types.py`、`snapshot_manager.py`、`snapshot_conflict.py`（问题6 + 7e）
- `gateway/permission_manager.py`（问题4 映射统一；7f 词边界）
- `gateway/permission_audit.py`（问题5 脱敏）
- `gateway/ws_server.py`（问题5 启动校验；7d token header 优先）
- `gateway/server.py`（问题5 RPC；7b 白名单校验；7c 模式审计）
- `tools/base_tool.py`（7a 移除线程跳过）

**新增（2 个模块 + 8 个测试）**：
- `agent_core/powershell_validator.py`、`agent_core/code_destructive_guard.py`
- `tests/`：test_tool_orchestrator_e2e / test_browser_download_sanitize / test_command_validation / test_permission_mapping / test_audit_hardening / test_snapshot_external_modification / test_minor_hardening / test_orchestration_cleanup

**图谱**：`knowledgesource/` content.md + 04/05/06 章 + VERSION。

## 验证证据

- 16/16 测试套件通过（8 新 + 8 既有；新测试共 150+ 断言，含恶意输入矩阵与静态守护）
- 16 个改动文件 py_compile 通过；20 个模块导入冒烟通过
- 关键回归守护：`tests/test_tool_orchestrator_e2e.py` 含"源码不得出现 context.state"静态断言

## 未完成事项 / 后续建议

1. **重启生效**：所有修复在源码；桌面版（exe）需重新打包才会带上修复。源码版重启即生效。
2. **exe 遗留 bug（已排查，源码已修复）**：旧 exe 写"敏感文件名"（含 token/key/password/.env/secret 等词）会报 `'AuditEvent' object has no attribute 'timestamp'`（旧代码用 `type('AuditEvent', (), {...})()` 假对象）。当前源码已用真 dataclass（已冒烟验证）；重启后消失。**重启前避免写敏感文件名**。
3. **MCP 状态**：源码版因 `.mcp.json` 占位符化 + 信任机制被跳过（0 servers）。建议把真实配置放 `~/.pebble/.mcp.json`（用户级覆盖，不需信任审批、不进仓库）。
4. **chat_stream 整体拆分**：1115 行 → 已提取 ~60 行；建议作为专项（高风险，需 LLM mock 测试设施）。
5. **CSP 'unsafe-eval' 收紧**：需前端完整回归后决定（本次未动）。
6. ~~agent_model_config.json 幽灵 agent~~ **已完成（2026-10-17 追加轮）**：analysis_agent / article_agent 全链路删除（runtime ~/.pebble 副本 + 项目根模板 + 前端 DEFAULT_AGENTS + Ch5 表格修正）；skills 目录数对齐（115 = 115，删除 2 个空目录 data-analysis / writing，Ch9 计数校准为 115/101）；vue-tsc 通过。

## 追加轮（2026-10-17）：幽灵 agent 清理 + skills 对齐

**修改**：`agent_model_config.json`（项目根，剩 code_agent）、`~/.pebble/agent_model_config.json`（runtime，剩 code_agent + chat_agent）、`ui-gui-vue3/src/stores/config.ts`（DEFAULT_AGENTS 删 2 行）、`knowledgesource/05-Agent-System.md`（Agent Definition Files 表修正）、`09-Configuration.md`（115/101）、`VERSION`。
**删除**：`skills/data-analysis/`、`skills/writing/`（空目录）。
**验证**：JSON 解析通过（两文件均无幽灵）、vue-tsc exit 0、skills diff 脚本（本地 115 = GitHub 115，零差异）。

## 建议技能（后续 Agent）

- 涉及工具/权限链修改：先读 `knowledgesource/06-Tool-System.md` + `04-Gateway-System.md`；改完跑 `python tests/test_tool_orchestrator_e2e.py` + 全量回归
- 涉及快照/回滚：读 `05-Agent-System.md` 的 snapshot 表 + 跑 `tests/test_snapshot_external_modification.py`
- 大型重构前：参考本轮方法（快照取证 → 静态守护断言 → 独立反馈回路）
