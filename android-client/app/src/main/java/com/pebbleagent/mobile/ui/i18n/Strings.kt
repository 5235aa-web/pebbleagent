package com.pebbleagent.mobile.ui.i18n

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Interface language, switchable at runtime from Settings.
 *
 * Keyed by the English string, exactly like the prototype's EN -> 中文 table: a
 * screen writes its text in English and `t()` swaps it when Chinese is selected,
 * so adding a language means extending one map instead of touching every call
 * site. Reading [lang] inside a composable subscribes it to the change, which is
 * what makes the switch take effect across every screen without a restart.
 */
object L {
    const val EN = "en"
    const val CN = "cn"

    var lang by mutableStateOf(EN)
        private set

    fun select(value: String) {
        lang = if (value == CN) CN else EN
    }

    /** Translation for an English source string; unknown keys pass through. */
    fun t(en: String): String = if (lang == CN) ZH[en] ?: en else en

    private val ZH = mapOf(
        // ---- bottom navigation / section titles ----------------------------
        "Sessions" to "会话",
        "Inbox" to "收件箱",
        "Changes" to "变更",
        "Settings" to "设置",
        "Usage" to "用量",
        "Conversation" to "对话",
        // ---- settings cards -------------------------------------------------
        "Connection" to "连接",
        "Workspace" to "工作区",
        "Agent" to "智能体",
        "Agent & Model" to "智能体与模型",
        "Project" to "项目",
        "Model" to "模型",
        "Theme" to "主题",
        "Appearance" to "外观",
        "Behaviour" to "行为",
        "Language" to "语言",
        "Text size" to "字体大小",
        "Permission Request" to "权限请求",
        "Notification sound" to "提示音",
        "Notifications" to "通知",
        "replies, approvals and questions" to "回复、审批与提问",
        "Reply ready" to "回复已完成",
        "New messages" to "新消息",
        "conversations updated" to "个会话有更新",
        "Question" to "提问",
        "Notify on completion" to "完成时通知",
        "push when a turn finishes" to "一轮对话结束时推送",
        "Approve from notification" to "从通知中审批",
        "risk level low only" to "仅低风险操作",
        "Keep screen awake" to "保持屏幕常亮",
        "while streaming" to "流式输出期间",
        // ---- connection ------------------------------------------------------
        "connected" to "已连接",
        "LAN connected" to "局域网已连接",
        "LAN mode" to "局域网模式",
        "Connecting" to "正在连接",
        "Reconnecting" to "正在重连",
        "Connection lost" to "连接已断开",
        "Send failed" to "发送失败",
        "Send test notification" to "发送测试通知",
        "Allow background running" to "允许后台运行",
        "Auto-start settings" to "自启动设置",
        "Disconnect" to "断开连接",
        "Scan QR from desktop" to "扫描桌面端二维码",
        "Pair with desktop" to "与桌面端配对",
        "or enter manually" to "或手动输入",
        "Enter link manually" to "手动输入链接",
        "Scan QR instead" to "改用扫码",
        "Choose from gallery" to "从相册选择",
        "Desktop address" to "桌面地址",
        "Pairing token" to "配对令牌",
        "Connect" to "连接",
        "Host" to "主机",
        "Port" to "端口",
        "Token" to "令牌",
        "Retry" to "重试",
        "tap to retry" to "点击重试",
        "Pairing failed" to "配对失败",
        "PAIRING LINK" to "配对链接",
        "OR ENTER MANUALLY" to "或手动输入",
        "MOBILE" to "移动端",
        // ---- conversation -----------------------------------------------------
        "Back" to "返回",
        "Rename" to "重命名",
        "Rename session" to "重命名会话",
        "Delete" to "删除",
        "Delete session?" to "删除会话？",
        "Save" to "保存",
        "Cancel" to "取消",
        "Skip" to "跳过",
        "Next" to "下一步",
        "Back to question" to "返回上一题",
        "Submit" to "提交",
        "Send" to "发送",
        "Stop" to "停止",
        "Need a decision" to "等待你的决定",
        "Thinking" to "思考中",
        "Message PebbleAgent..." to "发消息给 PebbleAgent...",
        "or describe a different approach..." to "或描述另一种做法...",
        "CHAT ONLY" to "仅聊天",
        "FULL TOOLS" to "完整工具",
        "Today" to "今天",
        "Yesterday" to "昨天",
        "Answer" to "回答",
        "Free text" to "自由输入",
        "Nothing waiting on you" to "没有待处理项",
        // ---- approval ---------------------------------------------------------
        "Allow" to "允许",
        "Deny" to "拒绝",
        "Allow all" to "全部允许",
        "Deny all" to "全部拒绝",
        "Remember for" to "记住",
        "Approve" to "通过",
        "approval" to "审批",
        "Approval" to "审批",
        "Permission" to "权限",
        "Operation" to "操作",
        "Target" to "目标",
        "Reason" to "原因",
        "Question" to "问题",
        "low risk" to "低风险",
        "risk" to "风险",
        "medium risk" to "中风险",
        "high risk" to "高风险",
        "critical risk" to "极高风险",
        "Read file" to "读取文件",
        "Write file" to "写入文件",
        "Delete file" to "删除文件",
        "Execute command" to "执行命令",
        "Network access" to "网络访问",
        "Network fetch" to "网络请求",
        "Launch app" to "启动应用",
        "Screenshot" to "截屏",
        "Read clipboard" to "读取剪贴板",
        "Write clipboard" to "写入剪贴板",
        "File read" to "读取文件",
        "File write" to "写入文件",
        "File delete" to "删除文件",
        "File create" to "新建文件",
        "Exec command" to "执行命令",
        "Command exec" to "执行命令",
        "Network fetch" to "网络请求",
        "App launch" to "启动应用",
        "Clipboard read" to "读取剪贴板",
        "Clipboard write" to "写入剪贴板",
        "Env var" to "环境变量",
        // ---- sessions ---------------------------------------------------------
        "search sessions" to "搜索会话",
        "No sessions yet" to "还没有会话",
        "Could not load sessions" to "无法加载会话",
        "loading..." to "加载中...",
        "messages" to "条消息",
        "questions" to "个问题",
        "file" to "个文件",
        "files" to "个文件",
        "changed" to "已变更",
        "allowed" to "已允许",
        "allowed once" to "已允许一次",
        "denied" to "已拒绝",
        "skipped" to "已跳过",
        "options" to "个选项",
        "free-form answer" to "自由输入",
        "min rule" to "分钟规则",
        "waiting approval" to "等待审批",
        // ---- inbox ------------------------------------------------------------
        "Pending" to "待处理",
        "Resolved" to "已处理",
        "needs answer" to "待回答",
        "Approvals and questions from any session show up here" to "所有会话的审批与提问都会出现在这里",
        "nothing waiting" to "没有待处理项",
        "pending" to "待处理",
        // ---- changes ----------------------------------------------------------
        "Files" to "文件",
        "Accept all" to "全部接受",
        "Revert" to "回退",
        "Reverted" to "已回退",
        "Accepted" to "已接受",
        "Could not load changes" to "无法加载变更",
        "No changes yet" to "还没有变更",
        "added" to "新增",
        "removed" to "删除",
        // ---- usage ------------------------------------------------------------
        "Session spend" to "本次会话花费",
        "cache hit" to "缓存命中",
        "cache miss" to "缓存未命中",
        "Breakdown" to "明细",
        "Calls" to "调用",
        "Call" to "调用",
        "Session" to "会话",
        "session" to "会话",
        "this session" to "本次会话",
        "total" to "合计",
        "input" to "输入",
        "output" to "输出",
        "Today so far" to "今日",
        "Last 7 days" to "近 7 天",
        "Last 30 days" to "近 30 天",
        "Could not load usage" to "无法加载用量",
        "No usage yet" to "还没有用量",
        "context" to "上下文",
        "CNY" to "人民币",
        // ---- model picker ------------------------------------------------------
        "Model picker" to "选择模型",
        "Select model" to "选择模型",
        "Could not load models" to "无法加载模型",
        "no key" to "未配置密钥",
        "API keys are managed on the desktop" to "API 密钥在桌面端管理",
        "per 1M" to "每百万",
        // ---- misc --------------------------------------------------------------
        "Pairing" to "配对中",
        "Ok" to "好",
        "OK" to "好",
        "Dismiss" to "关闭",
        "Loading" to "加载中",
        "Error" to "错误",
        "Copy" to "复制",
        "Copied" to "已复制",
        "Show more" to "展开",
        "Show less" to "收起",
        // ---- long-form and status text -------------------------------------
        "This removes the transcript and its cost records. It cannot be undone." to "这会删除对话记录及其费用记录，无法撤销。",
        "Accept failed" to "接受失败",
        "Accepted all changes" to "已接受全部变更",
        "No diff" to "无差异",
        "No file changes in this session" to "本次会话没有文件变更",
        "loading…" to "加载中…",
        "no changes yet" to "还没有变更",
        "session snapshot" to "会话快照",
        "30 days" to "30 天",
        "7 days" to "7 天",
        "No calls recorded" to "还没有调用记录",
        "SESSION SPEND" to "本次会话花费",
        "no data" to "没有数据",
        "1 hour" to "1 小时",
        "15 min" to "15 分钟",
        "trust for the rest of this session" to "本会话内信任",
        "Pairing…" to "配对中…",
        "host + port + code" to "主机 + 端口 + 配对码",
    )
}

/**
 * Global text scale, applied by overriding `LocalDensity.fontScale`.
 *
 * Every `sp` size in the app resolves through the density, so scaling it here
 * enlarges text everywhere, including inside cards and sheets, without touching
 * a single layout constant. Steps match what the prototype calls out: compact,
 * default, large, larger.
 */
object FontScale {
    val steps = listOf(0.9f, 1.0f, 1.15f, 1.3f)

    /** Labels are translated through [L], so they follow the interface language. */
    val labels = listOf("Compact", "Default", "Large", "Larger")

    val labelZh = listOf("紧凑", "标准", "大", "特大")

    var value by mutableStateOf(1.0f)
        private set

    fun select(scale: Float) {
        value = steps.minByOrNull { kotlin.math.abs(it - scale) } ?: 1.0f
    }

    /** Index of the current step, for the segmented control. */
    val index: Int get() = steps.indexOfFirst { it == value }.coerceAtLeast(1)
}
