export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled'
export interface TodoItem { id: string; content: string; status: TodoStatus; priority?: 'high' | 'medium' | 'low' }
export interface QuestionOption { label: string; detail: string }
export interface QuestionItem { prompt: string; title: string; options: QuestionOption[]; multiple?: boolean }
export type ReasoningEffort = 'low' | 'medium' | 'high' | 'max'
export interface LLMProvider { id: string; provider: string; apiKey: string; baseUrl: string; models: string[]; temperature: number; maxTokens: number; topP: number; reasoningEffort?: ReasoningEffort; thinkingEnabled?: boolean }
export interface AgentInfo { name: string; displayName: string; description: string; modelSource: string }
export type ToolStatus = 'streaming' | 'running' | 'completed' | 'failed' | 'cancelled'
export interface ToolCall { _toolId?: string; status: ToolStatus; label: string; duration: number; files?: string[]; input?: string; output?: string; error?: string }
export type ExecutionItemType = 'reasoning' | 'text' | 'tool' | 'question'
export interface ExecutionOrderItem { id: string; type: ExecutionItemType; content?: string; toolInfo?: ToolCall; questionId?: string; questions?: QuestionItem[]; state?: 'answered' | 'skipped'; startTime?: number; durationMs?: number }
export interface Message { id: string; messageId?: number; role: 'user' | 'agent'; content: string; reasoning?: string; createdAt?: number; interrupted?: boolean; toolCalls?: ToolCall[]; executionOrder?: ExecutionOrderItem[]; codeBlocks?: { code: string; language?: string }[]; htmlPreviews?: { html: string }[]; questionCard?: { id: string; questions: QuestionItem[]; state?: 'answered' | 'skipped' }; summaryCard?: string; isCompacted?: boolean; isCompactBoundary?: boolean; attachments?: { name: string; size: number; icon: string; dataUrl?: string; isImage?: boolean }[] }
export interface FileChangeItem { id: string; relativePath: string; operationType: 'create'|'modify'|'delete'; toolName: string; toolId: string; addedLines?: number; removedLines?: number; status?: string; hunks?: DiffHunk[]; expanded: boolean; reverted: boolean; loadingDiff: boolean; activeCount?: number; revertError?: string }
export interface DiffLine { prefix: string; text: string }
export interface DiffHunk { old_start: number; old_lines: number; new_start: number; new_lines: number; lines: DiffLine[] }
