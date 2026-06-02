#!/usr/bin/env python3
"""Convert WebVTT (incl. YouTube auto-caption rolling-buffer format) to clean Markdown.

YouTube auto-captions have a 2-line rolling-buffer pattern:
  Segment N text:   "previous line\nnew line with <c>inline timing tags</c>"
  Segment N+1 text: "new line plain"   (echo, short duration)

We exploit this: for each rolling segment, only the LAST line is new — and we strip
inline <c>...</c> tags to get plain text. Then we deduplicate against the previous emitted line.

For non-YT VTTs (whisper.cpp output, manual subtitles), each segment has one text line
without rolling, so the dedupe simply does nothing.

Usage: vtt_to_md.py <input.vtt>
"""
import re
import sys
from pathlib import Path


def parse_vtt(text: str):
    """Yield (start_s, end_s, text) tuples. For rolling-buffer VTTs, returns only the new line."""
    blocks = re.split(r'\n\n+', text)
    for block in blocks:
        lines = [ln.rstrip() for ln in block.strip().split('\n')]
        if not lines or lines[0].startswith(('WEBVTT', 'Kind:', 'Language:', 'NOTE')):
            continue

        ts_idx = None
        for i, line in enumerate(lines):
            if '-->' in line:
                ts_idx = i; break
        if ts_idx is None:
            continue

        m = re.match(
            r'(\d+):(\d+):(\d+)\.(\d+)\s+-->\s+(\d+):(\d+):(\d+)\.(\d+)',
            lines[ts_idx]
        )
        if not m:
            continue
        sh, sm, ss, sms, eh, em, es, ems = map(int, m.groups())
        start_s = sh*3600 + sm*60 + ss + sms/1000
        end_s = eh*3600 + em*60 + es + ems/1000

        text_lines = lines[ts_idx+1:]
        # Filter empty
        text_lines = [tl for tl in text_lines if tl.strip()]
        if not text_lines:
            continue

        # YT rolling-buffer detection: last line has inline <c>...</c> tags = real new content
        # If only one text line and no tags, treat as final segment
        has_tags_anywhere = any('<c>' in tl or re.search(r'<\d+:\d+:\d+\.\d+>', tl) for tl in text_lines)

        if has_tags_anywhere:
            # The "new" content is the LAST text line (contains the inline tags)
            new_line = text_lines[-1]
        else:
            # Whisper or manual sub: just concatenate
            new_line = ' '.join(text_lines)

        # Strip inline timing/color tags
        new_line = re.sub(r'<\d+:\d+:\d+\.\d+>', '', new_line)
        new_line = re.sub(r'<c[^>]*>', '', new_line)
        new_line = new_line.replace('</c>', '')
        new_line = new_line.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<')
        new_line = re.sub(r'\s+', ' ', new_line).strip()

        if new_line:
            yield (start_s, end_s, new_line)


def format_ts(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m >= 60:
        h = m // 60
        m = m % 60
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m:02d}:{s:02d}'


def to_markdown(segments):
    if not segments:
        return '_(empty transcript)_'

    out = []
    last_line = ''
    last_end = -10.0
    last_ts_marker = -100.0
    paragraph_buffer = []
    paragraph_start = 0

    def flush_paragraph(ts_seconds):
        if paragraph_buffer:
            text = ' '.join(paragraph_buffer).strip()
            text = re.sub(r'\s+', ' ', text)
            out.append(f'**[{format_ts(ts_seconds)}]** {text}')
            out.append('')
            paragraph_buffer.clear()

    for start, end, text in segments:
        # Dedup: exact repeat of last line
        if text == last_line:
            continue
        # Dedup: rolling-buffer echo (current text is prefix-overlap of last)
        # Heuristic: if text is contained in last_line OR last_line ends with text
        if last_line and (text in last_line or last_line.endswith(text)):
            continue

        # Paragraph break on long pause
        gap = start - last_end
        if gap > 3.0 and paragraph_buffer:
            flush_paragraph(paragraph_start)
            paragraph_start = start
        if not paragraph_buffer:
            paragraph_start = start

        paragraph_buffer.append(text)
        last_line = text
        last_end = end

        # Periodic flush every ~60 s to avoid huge wall-of-text paragraphs
        if start - paragraph_start > 60 and len(paragraph_buffer) > 8:
            flush_paragraph(paragraph_start)
            paragraph_start = start

    if paragraph_buffer:
        flush_paragraph(paragraph_start)

    return '\n'.join(out).strip()


def main():
    if len(sys.argv) < 2:
        print('Usage: vtt_to_md.py <input.vtt>', file=sys.stderr)
        sys.exit(1)
    vtt_path = Path(sys.argv[1])
    if not vtt_path.exists():
        print(f'File not found: {vtt_path}', file=sys.stderr)
        sys.exit(2)
    text = vtt_path.read_text(encoding='utf-8', errors='replace')
    segs = list(parse_vtt(text))
    print(to_markdown(segs))


if __name__ == '__main__':
    main()
