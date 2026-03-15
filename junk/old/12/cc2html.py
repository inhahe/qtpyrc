#!/usr/bin/env python3
"""Convert a Claude Code conversation JSONL file to a readable HTML file.

Usage:  python cc2html.py <conversation.jsonl> [output.html]

If output.html is omitted, writes to <conversation>.html.
"""

import json, sys, os, html, re

# ---------------------------------------------------------------------------
# Markdown-subset to HTML converter (inline only, no full parser needed)
# ---------------------------------------------------------------------------

def _md_inline(text):
    """Convert basic markdown inline formatting to HTML."""
    # Escape HTML first
    text = html.escape(text)
    # Bold + italic ***text*** or ___text___
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text)
    # Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    # Italic *text* or _text_ (but not inside words for _)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)
    # Inline code `text`
    text = re.sub(r'`([^`]+?)`', r'<code>\1</code>', text)
    # Links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


def _md_to_html(text):
    """Convert a markdown text block to HTML paragraphs."""
    lines = text.split('\n')
    out = []
    in_code = False
    code_lang = ''
    code_lines = []
    in_list = False
    in_table = False
    table_rows = []

    def _flush_table():
        nonlocal in_table, table_rows
        if not table_rows:
            return
        out.append('<table>')
        for ri, row in enumerate(table_rows):
            tag = 'th' if ri == 0 else 'td'
            out.append('<tr>')
            for cell in row:
                out.append('<%s>%s</%s>' % (tag, _md_inline(cell.strip()), tag))
            out.append('</tr>')
        out.append('</table>')
        in_table = False
        table_rows = []

    for line in lines:
        stripped = line.strip()

        # Fenced code blocks
        if stripped.startswith('```'):
            if in_code:
                out.append('<pre><code class="language-%s">%s</code></pre>'
                           % (html.escape(code_lang),
                              html.escape('\n'.join(code_lines))))
                in_code = False
                code_lines = []
                code_lang = ''
            else:
                _flush_table()
                if in_list:
                    out.append('</ul>')
                    in_list = False
                in_code = True
                code_lang = stripped[3:].strip()
            continue
        if in_code:
            code_lines.append(line)
            continue

        # Table rows
        if '|' in stripped and stripped.startswith('|') and stripped.endswith('|'):
            cells = [c for c in stripped.split('|')[1:-1]]
            # Skip separator rows (| --- | --- |)
            if all(re.match(r'^[\s:?-]+$', c) for c in cells):
                continue
            if not in_table:
                if in_list:
                    out.append('</ul>')
                    in_list = False
                in_table = True
            table_rows.append(cells)
            continue
        elif in_table:
            _flush_table()

        # Blank line
        if not stripped:
            if in_list:
                out.append('</ul>')
                in_list = False
            out.append('')
            continue

        # Headers
        m = re.match(r'^(#{1,6})\s+(.+)', stripped)
        if m:
            if in_list:
                out.append('</ul>')
                in_list = False
            level = len(m.group(1))
            out.append('<h%d>%s</h%d>' % (level, _md_inline(m.group(2)), level))
            continue

        # Unordered list
        if re.match(r'^[-*+]\s', stripped):
            if not in_list:
                out.append('<ul>')
                in_list = True
            item = re.sub(r'^[-*+]\s+', '', stripped)
            out.append('<li>%s</li>' % _md_inline(item))
            continue

        # Ordered list
        m = re.match(r'^(\d+)[.)]\s+(.+)', stripped)
        if m:
            if not in_list:
                out.append('<ul>')
                in_list = True
            out.append('<li>%s</li>' % _md_inline(m.group(2)))
            continue

        # Horizontal rule
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            out.append('<hr>')
            continue

        # Regular paragraph line
        if in_list:
            out.append('</ul>')
            in_list = False
        out.append('<p>%s</p>' % _md_inline(stripped))

    # Close any open blocks
    if in_code:
        out.append('<pre><code>%s</code></pre>' % html.escape('\n'.join(code_lines)))
    if in_list:
        out.append('</ul>')
    _flush_table()

    return '\n'.join(out)


# ---------------------------------------------------------------------------
# JSONL processing
# ---------------------------------------------------------------------------

def _extract_text_blocks(content):
    """Extract content blocks from a message's content field."""
    if isinstance(content, str):
        return [('text', content)]
    blocks = []
    for block in (content or []):
        if not isinstance(block, dict):
            continue
        btype = block.get('type', '')
        if btype == 'text':
            blocks.append(('text', block.get('text', '')))
        elif btype == 'thinking':
            t = block.get('thinking', '')
            if t:
                blocks.append(('thinking', t))
        elif btype == 'tool_use':
            name = block.get('name', '')
            inp = block.get('input', {})
            blocks.append(('tool_use', name, inp))
        elif btype == 'tool_result':
            sub_content = block.get('content', '')
            if isinstance(sub_content, str):
                if sub_content:
                    blocks.append(('tool_result', sub_content))
            elif isinstance(sub_content, list):
                parts = []
                for sub in sub_content:
                    if isinstance(sub, dict) and sub.get('type') == 'text':
                        parts.append(sub.get('text', ''))
                if parts:
                    blocks.append(('tool_result', '\n'.join(parts)))
    return blocks


def convert(jsonl_path, html_path):
    """Convert a JSONL conversation to HTML."""
    messages = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_type = obj.get('type', '')
            if msg_type in ('file-history-snapshot', 'progress', 'system'):
                continue
            if msg_type not in ('user', 'assistant'):
                continue
            # Skip sidechain messages
            if obj.get('isSidechain'):
                continue
            messages.append(obj)

    # Deduplicate: assistant messages with the same requestId may appear
    # multiple times as streaming updates. Keep only the last one per uuid.
    seen_uuids = {}
    for msg in messages:
        uuid = msg.get('uuid', '')
        if uuid:
            seen_uuids[uuid] = msg
    # Rebuild in order, deduplicated
    final = []
    seen = set()
    for msg in messages:
        uuid = msg.get('uuid', '')
        if uuid in seen:
            continue
        seen.add(uuid)
        if uuid in seen_uuids:
            final.append(seen_uuids[uuid])

    # Build a flat list of (is_collapsible, label, html) items
    items = []

    for msg in final:
        msg_type = msg.get('type', '')
        inner = msg.get('message', {})
        content = inner.get('content', '')
        blocks = _extract_text_blocks(content)

        if not blocks:
            continue

        if msg_type == 'user':
            for btype, *bdata in blocks:
                if btype == 'text':
                    h = ('<div class="msg user">'
                         '<div class="role">You</div>'
                         '<div class="content">%s</div>'
                         '</div>' % _md_to_html(bdata[0]))
                    items.append((False, None, h))
                elif btype == 'tool_result':
                    text = bdata[0]
                    if len(text) > 2000:
                        text = text[:2000] + '\n... (truncated)'
                    h = ('<details class="tool-result">'
                         '<summary>Tool Result</summary>'
                         '<pre class="tool-output">%s</pre>'
                         '</details>' % html.escape(text))
                    items.append((True, 'Tool Result', h))

        elif msg_type == 'assistant':
            for btype, *bdata in blocks:
                if btype == 'text':
                    h = ('<div class="msg assistant">'
                         '<div class="role">Claude</div>'
                         '<div class="content">%s</div>'
                         '</div>' % _md_to_html(bdata[0]))
                    items.append((False, None, h))
                elif btype == 'thinking':
                    h = ('<details class="thinking">'
                         '<summary>Thinking...</summary>'
                         '<pre>%s</pre>'
                         '</details>' % html.escape(bdata[0]))
                    items.append((True, 'Thinking', h))
                elif btype == 'tool_use':
                    name, inp = bdata
                    summary = _tool_summary(name, inp)
                    h = ('<details class="tool-call">'
                         '<summary>Tool: %s</summary>'
                         '<pre class="tool-input">%s</pre>'
                         '</details>' % (html.escape(name), html.escape(summary)))
                    items.append((True, name, h))

    # Group consecutive collapsible items into wrapper <details> when > 1
    parts = [HTML_HEAD]
    i = 0
    while i < len(items):
        is_coll, label, h = items[i]
        if not is_coll:
            parts.append(h)
            i += 1
            continue
        # Count consecutive collapsible items
        run = []
        while i < len(items) and items[i][0]:
            run.append(items[i])
            i += 1
        if len(run) == 1:
            parts.append(run[0][2])
        else:
            # Summarize: count by label
            counts = {}
            for _, lbl, _ in run:
                counts[lbl] = counts.get(lbl, 0) + 1
            summary_parts = []
            for lbl, cnt in counts.items():
                if cnt > 1:
                    summary_parts.append('%s x%d' % (lbl, cnt))
                else:
                    summary_parts.append(lbl)
            summary = ', '.join(summary_parts)
            parts.append('<details class="tool-group">')
            parts.append('<summary>%d tool steps (%s)</summary>' % (len(run), html.escape(summary)))
            for _, _, inner_h in run:
                parts.append(inner_h)
            parts.append('</details>')

    parts.append('</body></html>')

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
    print('Wrote %s (%d messages)' % (html_path, len(final)))


def _tool_summary(name, inp):
    """Format tool input as a concise summary."""
    if name in ('Read', 'Glob', 'Grep'):
        return json.dumps(inp, indent=2, ensure_ascii=False)
    if name == 'Edit':
        path = inp.get('file_path', '')
        old = inp.get('old_string', '')
        new = inp.get('new_string', '')
        lines = ['file: %s' % path]
        if old:
            preview = old if len(old) <= 300 else old[:300] + '...'
            lines.append('old: %s' % preview)
        if new:
            preview = new if len(new) <= 300 else new[:300] + '...'
            lines.append('new: %s' % preview)
        return '\n'.join(lines)
    if name == 'Write':
        path = inp.get('file_path', '')
        content = inp.get('content', '')
        preview = content if len(content) <= 500 else content[:500] + '...'
        return 'file: %s\n%s' % (path, preview)
    if name == 'Bash':
        cmd = inp.get('command', '')
        desc = inp.get('description', '')
        if desc:
            return '# %s\n%s' % (desc, cmd)
        return cmd
    if name == 'Agent':
        desc = inp.get('description', '')
        prompt = inp.get('prompt', '')
        preview = prompt if len(prompt) <= 300 else prompt[:300] + '...'
        return '%s\n%s' % (desc, preview)
    # Fallback
    return json.dumps(inp, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_HEAD = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Claude Code Conversation</title>
<style>
:root {
  --bg: #1a1a2e;
  --fg: #e0e0e0;
  --user-bg: #16213e;
  --user-border: #0f3460;
  --asst-bg: #1a1a2e;
  --asst-border: #533483;
  --tool-bg: #0d1117;
  --tool-border: #30363d;
  --result-bg: #0d1117;
  --thinking-bg: #1c1c1c;
  --code-bg: #0d1117;
  --accent: #e94560;
  --link: #58a6ff;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.6;
  padding: 20px;
  max-width: 960px;
  margin: 0 auto;
}
.msg {
  margin: 12px 0;
  padding: 12px 16px;
  border-radius: 8px;
  border-left: 4px solid;
}
.msg.user {
  background: var(--user-bg);
  border-color: var(--user-border);
}
.msg.assistant {
  background: var(--asst-bg);
  border-color: var(--asst-border);
}
details.tool-call {
  margin: 8px 0;
  background: var(--tool-bg);
  border-radius: 6px;
  border: 1px solid var(--tool-border);
  font-size: 0.9em;
}
details.tool-call summary {
  padding: 8px 12px;
  cursor: pointer;
  color: #f0883e;
  font-weight: 700;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
details.tool-result {
  margin: 8px 0;
  background: var(--result-bg);
  border-radius: 6px;
  border: 1px solid #238636;
  font-size: 0.9em;
}
details.tool-result summary {
  padding: 8px 12px;
  cursor: pointer;
  color: #3fb950;
  font-weight: 700;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.role {
  font-weight: 700;
  font-size: 0.85em;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
  color: #888;
}
.msg.user .role { color: #4a9eff; }
.msg.assistant .role { color: var(--accent); }
.content p { margin: 6px 0; }
.content h1, .content h2, .content h3,
.content h4, .content h5, .content h6 {
  margin: 12px 0 6px;
  color: #fff;
}
.content ul { margin: 6px 0 6px 24px; }
.content li { margin: 2px 0; }
.content table {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 0.95em;
}
.content th, .content td {
  border: 1px solid #444;
  padding: 4px 10px;
  text-align: left;
}
.content th { background: #222; }
.content hr {
  border: none;
  border-top: 1px solid #444;
  margin: 12px 0;
}
pre, code {
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
}
code {
  background: var(--code-bg);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.92em;
}
pre {
  background: var(--code-bg);
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
  font-size: 0.88em;
  line-height: 1.5;
}
pre code {
  background: none;
  padding: 0;
}
.tool-input, .tool-output {
  max-height: 400px;
  overflow: auto;
}
details.thinking {
  margin: 8px 0;
  background: var(--thinking-bg);
  border-radius: 6px;
  border: 1px solid #333;
}
details.thinking summary {
  padding: 8px 12px;
  cursor: pointer;
  color: #888;
  font-style: italic;
  font-size: 0.9em;
}
details.thinking pre {
  margin: 0;
  padding: 12px;
  font-size: 0.82em;
  max-height: 500px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
details.tool-group {
  margin: 8px 0;
  background: var(--tool-bg);
  border-radius: 6px;
  border: 1px solid var(--tool-border);
}
details.tool-group summary {
  padding: 8px 12px;
  cursor: pointer;
  color: #8b949e;
  font-weight: 700;
  font-size: 0.85em;
}
details.tool-group > details {
  margin: 0 8px 4px;
}
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1 style="text-align:center; color:#fff; margin-bottom:24px;">Claude Code Conversation</h1>
'''


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python cc2html.py <conversation.jsonl> [output.html]')
        sys.exit(1)
    src = sys.argv[1]
    if not os.path.isfile(src):
        print('Error: file not found: %s' % src, file=sys.stderr)
        sys.exit(1)
    if len(sys.argv) > 2:
        dst = sys.argv[2]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dst = os.path.join(script_dir, os.path.splitext(os.path.basename(src))[0] + '.html')
    convert(src, dst)
