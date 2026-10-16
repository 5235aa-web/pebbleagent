export type BlockType =
  | 'text'
  | 'code'
  | 'incomplete-code'
  | 'html'
  | 'incomplete-html'
  | 'html-file'
  | 'svg'
  | 'incomplete-svg'
  | 'mermaid'
  | 'incomplete-mermaid';

export interface ContentBlock {
  type: BlockType;
  content: string;
  language?: string;
  lines?: number;
  id?: string;
}

/**
 * 解析 Markdown 文本为内容块列表
 * 支持：```html、```svg、```mermaid、```任意语言、普通文本
 * 同时检测流式输出中的未闭合 ``` 块
 */
export function parseContent(content: string): ContentBlock[] {
  if (!content) return [];

  const blocks: ContentBlock[] = [];
  const specialBlocks: { start: number; end: number; block: ContentBlock }[] = [];

  // ===== 1. 提取完整 ``` 代码块 =====
  const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;
  let match: RegExpExecArray | null;
  while ((match = codeBlockRegex.exec(content)) !== null) {
    const language = match[1] || 'code';
    const code = match[2].trim();
    const lines = code.split('\n').length;
    const langLower = language.toLowerCase();

    if (langLower === 'html' || langLower === 'htm') {
      specialBlocks.push({
        start: match.index,
        end: match.index + match[0].length,
        block: { type: 'html', content: code, language },
      });
    } else if (langLower === 'svg') {
      specialBlocks.push({
        start: match.index,
        end: match.index + match[0].length,
        block: { type: 'svg', content: code, language },
      });
    } else if (langLower === 'mermaid') {
      specialBlocks.push({
        start: match.index,
        end: match.index + match[0].length,
        block: {
          type: 'mermaid',
          content: code,
          language: 'mermaid',
          id: `mermaid_${match.index}`,
        },
      });
    } else {
      specialBlocks.push({
        start: match.index,
        end: match.index + match[0].length,
        block: { type: 'code', content: code, language, lines },
      });
    }
  }

  // ===== 2. 检测流式输出中未闭合的 ``` 块 =====
  const lastOpen = content.lastIndexOf('```');
  if (lastOpen !== -1) {
    const afterStart = content.slice(lastOpen);
    const hasClosing = afterStart.indexOf('```', 3) !== -1;
    if (!hasClosing) {
      const alreadyMatched = specialBlocks.some((sb) => sb.end >= content.length);
      if (!alreadyMatched) {
        const remaining = content.slice(lastOpen);
        const langMatch = remaining.match(/```(\w*)\n/);
        const language = langMatch ? langMatch[1] : 'code';
        const code = remaining.replace(/```\w*\n/, '');
        const langLower = language.toLowerCase();

        if (langLower === 'html' || langLower === 'htm') {
          specialBlocks.push({
            start: lastOpen,
            end: content.length,
            block: { type: 'incomplete-html', content: code, language },
          });
        } else if (langLower === 'svg') {
          specialBlocks.push({
            start: lastOpen,
            end: content.length,
            block: { type: 'incomplete-svg', content: code, language },
          });
        } else if (langLower === 'mermaid') {
          specialBlocks.push({
            start: lastOpen,
            end: content.length,
            block: {
              type: 'incomplete-mermaid',
              content: code,
              language: 'mermaid',
              id: `mermaid_incomplete_${lastOpen}`,
            },
          });
        } else {
          specialBlocks.push({
            start: lastOpen,
            end: content.length,
            block: { type: 'incomplete-code', content: code, language },
          });
        }
      }
    }
  }

  // ===== 3. 排序 =====
  specialBlocks.sort((a, b) => a.start - b.start);

  // ===== 4. 构建最终块列表（文本 + 特殊块交错） =====
  let lastIndex = 0;
  for (const { start, end, block } of specialBlocks) {
    if (start > lastIndex) {
      const text = content.slice(lastIndex, start).trim();
      if (text) {
        const filtered = filterInvalidMarkers(text);
        if (filtered) {
          blocks.push({ type: 'text', content: filtered });
        }
      }
    }
    blocks.push(block);
    lastIndex = end;
  }

  // ===== 5. 剩余文本 =====
  if (lastIndex < content.length) {
    const hasIncomplete = specialBlocks.some(
      (sb) =>
        sb.block.type === 'incomplete-code' ||
        sb.block.type === 'incomplete-html' ||
        sb.block.type === 'incomplete-svg' ||
        sb.block.type === 'incomplete-mermaid'
    );
    if (!hasIncomplete) {
      const text = content.slice(lastIndex).trim();
      if (text) {
        const filtered = filterInvalidMarkers(text);
        if (filtered) {
          blocks.push({ type: 'text', content: filtered });
        }
      }
    }
  }

  return extractHtmlFileBlocks(blocks);
}

/**
 * 检测文本中的 @pebble-file 标记，将其转换为 html-file 块。
 * 在 parseContent 返回前调用。
 */
function extractHtmlFileBlocks(blocks: ContentBlock[]): ContentBlock[] {
  const PEB_RE = /@pebble-file:\s*([\w.\-/]+)/g
  const result: ContentBlock[] = []
  for (const b of blocks) {
    if (b.type !== 'text') { result.push(b); continue }
    let text = b.content
    let match: RegExpExecArray | null
    let lastIdx = 0
    const local: ContentBlock[] = []
    while ((match = PEB_RE.exec(text)) !== null) {
      const before = text.slice(lastIdx, match.index).trim()
      if (before) local.push({ type: 'text', content: before })
      // console.log('[contentParser] @pebble-file detected:', match[1]) // 调试：如有问题请开启
      local.push({ type: 'html-file', content: match[1] })
      lastIdx = match.index + match[0].length
    }
    if (local.length === 0) { result.push(b); continue }
    const after = text.slice(lastIdx).trim()
    if (after) local.push({ type: 'text', content: after })
    result.push(...local)
  }
  return result
}

/**
 * 过滤无效的标记（如单独的 ```tool）
 */
function filterInvalidMarkers(text: string): string {
  let filtered = text
    .replace(/```tool\s*/gi, '')
    .replace(/```\s*tool\s*```/gi, '')
    .replace(/```\s*```/g, '');
  return filtered.trim();
}
