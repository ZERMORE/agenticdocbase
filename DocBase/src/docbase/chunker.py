from __future__ import annotations

import re
from pathlib import Path
from .common import DocBaseError, digest


def is_prose(block):
    text = block['text'].strip()
    return (block['type'] in {'paragraph', 'ocr_text'} and len(text) >= 45
            and (len(re.findall(r'[A-Za-z]+', text)) >= 8 or len(re.findall(r'[\u4e00-\u9fff]', text)) >= 20)
            and '<latexit' not in text and not re.search(r'[A-Za-z0-9+/]{70,}', text))


def adjacent_prose(previous, block):
    if not is_prose(previous) or not is_prose(block):
        return False
    a, b = previous['source'].get('bbox'), block['source'].get('bbox')
    if a and b:
        if abs(a[0] - b[0]) > 30 or not -3 <= b[1] - a[3] <= 30:
            return False
        if min(a[2] - a[0], b[2] - b[0]) < .5 * max(a[2] - a[0], b[2] - b[0]):
            return False
    return True


class LengthBudget:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tokenizer = None
        if cfg.tokenizer_path:
            try:
                from tokenizers import Tokenizer
                self.tokenizer = Tokenizer.from_file(cfg.tokenizer_path)
            except Exception as exc:
                raise DocBaseError('TOKENIZER', 'tokenizer JSON 无法加载；安装 .[tokenizer] 并配置有效路径') from exc

    def count(self, text):
        return len(self.tokenizer.encode(text).ids) if self.tokenizer else len(text)

    def windows(self, text, reserve=0):
        limit = self.cfg.chunk_size - reserve
        if limit < 50:
            raise DocBaseError('CHUNK', '标题前缀过长，无法满足切片预算')
        start = 0
        while start < len(text):
            end = min(len(text), start + limit)
            if self.tokenizer:
                lo, hi = start + 1, len(text)
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if self.count(text[start:mid]) <= limit:
                        end, lo = mid, mid + 1
                    else:
                        hi = mid - 1
            if end < len(text):
                candidate = max(text.rfind(s, start + (end - start) // 2, end) for s in ['\n', '。', '；', '. '])
                if candidate > start:
                    end = candidate + 1
            yield start, end
            if end == len(text):
                break
            # Overlap uses characters in char mode; shrink in token mode until within budget.
            next_start = max(start + 1, end - min(self.cfg.overlap, (end - start) // 3))
            while self.tokenizer and self.count(text[next_start:end]) > self.cfg.overlap:
                next_start += 1
            start = next_start


def _make_block_chunks(artifacts, cfg):
    budget = LengthBudget(cfg)
    chunks = []
    for artifact in artifacts:
        headings = []
        for block in artifact['blocks']:
            if block['type'] == 'heading':
                headings = [block['text'][:160]]
                continue
            raw = block['text']
            left_trim = len(raw) - len(raw.lstrip())
            text = raw.strip()
            if not text:
                continue
            heading_path = block.get('heading_path') or headings
            prefix = ' / '.join(heading_path)[0:160]
            prefix = prefix + '\n' if prefix else ''
            pieces = []
            # Repeat table headers on each row-group slice; keep large individual rows traceable.
            if block['type'] == 'table' and '\n' in text and budget.count(prefix + text) > cfg.chunk_size:
                lines = text.splitlines(keepends=True)
                header = ''.join(lines[:2])
                if budget.count(prefix + header) < cfg.chunk_size - 50:
                    offset = len(header)
                    for row in lines[2:]:
                        for begin, end in budget.windows(row, budget.count(prefix + header)):
                            pieces.append((offset + begin, offset + end, header + row[begin:end], [0, len(header)]))
                        offset += len(row)
            if not pieces:
                pieces = [(start, end, text[start:end], None) for start, end in budget.windows(text, budget.count(prefix))]
            for start, end, content, header_range in pieces:
                source = dict(block['source'])
                source.update(doc_id=artifact['manifest']['doc_id'], revision=artifact['manifest']['revision'],
                              source_block_id=block['block_id'], source_bundle_id=artifact['bundle_id'])
                origin = {'figure_description': 'model_description', 'vision_transcription': 'model_transcription',
                          'ocr_text': 'ocr'}.get(block['type'], 'native_text')
                body = prefix + content
                identity = [artifact['artifact_id'], cfg.chunk_config(), block['block_id'], start, end, body]
                chunks.append({'chunk_id': 'c_' + digest(identity)[:32], 'artifact_id': artifact['artifact_id'],
                               'doc_id': artifact['manifest']['doc_id'],
                               'category': artifact.get('category_override') or block.get('category', '未分类'),
                               'text': content, 'search_text': body, 'heading_path': heading_path,
                               'type': block['type'], 'length': budget.count(body),
                               'length_unit': cfg.chunk_config()['length_unit'],
                               'block_ranges': [{'block_id': block['block_id'], 'start': left_trim + start, 'end': left_trim + end,
                                                 'repeated_header_range': [left_trim + v for v in header_range] if header_range else None}],
                               'content_origin': [origin], 'sources': [source],
                               'quality_warnings': artifact['manifest'].get('warnings', []) +
                                   (['doc2md 文档为 partial，仅索引成功块'] if artifact['manifest']['status'] == 'partial' else [])})
    return chunks


def make_chunks(artifacts, cfg):
    if cfg.chunk_strategy == 'block-v1':
        return _make_block_chunks(artifacts, cfg)
    # Opt-in grouping keeps old indices/configuration byte-for-byte compatible.
    output = []
    budget = LengthBudget(cfg)
    for artifact in artifacts:
        groups, pending, heading = [], [], []

        def clean(values):
            return [v for v in values if not re.fullmatch(r'\s*\d+\s*', v)
                    and not re.match(r'\s*arxiv\s*:', v, flags=re.I)]

        def flush():
            if pending:
                groups.append(list(pending))
                pending.clear()

        for original in artifact['blocks']:
            block = dict(original)
            if re.fullmatch(r'\s*(?:\d+[. ]+)?(?:references|bibliography|参考文献)\s*', block['text'], flags=re.I):
                flush()
                heading = ['References']
                continue
            if block['type'] == 'heading':
                useful = clean([block['text']])
                if useful:
                    flush()
                    heading = useful
                continue
            block['heading_path'] = (heading if heading == ['References'] else clean(block.get('heading_path') or []) or heading)
            joinable = is_prose(block)
            if pending:
                previous = pending[-1]
                compatible = joinable and adjacent_prose(previous, block) and previous['type'] == block['type'] and previous['heading_path'] == block['heading_path']
                compatible = compatible and previous['source'].get('page_index') == block['source'].get('page_index')
                text = '\n\n'.join(b['text'] for b in pending + [block])
                if not compatible or budget.count(text) > cfg.chunk_size - 160:
                    flush()
            pending.append(block)
            if not joinable:
                flush()
        flush()
        for group in groups:
            cursor, spans = 0, []
            for b in group:
                spans.append((cursor, cursor + len(b['text']), b))
                cursor += len(b['text']) + 2
            merged = dict(group[0], text='\n\n'.join(b['text'] for b in group),
                          block_id='group_' + digest([b['block_id'] for b in group])[:24])
            for chunk in _make_block_chunks([dict(artifact, blocks=[merged])], cfg):
                ranges = chunk['block_ranges']
                # Tables keep their original block identity and repeated-header offsets.
                if len(group) == 1:
                    for r in ranges:
                        r['block_id'] = group[0]['block_id']
                    chunk['sources'][0]['source_block_id'] = group[0]['block_id']
                else:
                    start, end = ranges[0]['start'], ranges[0]['end']
                    ranges, sources = [], []
                    for left, right, b in spans:
                        if max(start, left) >= min(end, right):
                            continue
                        ranges.append({'block_id': b['block_id'], 'start': max(start, left) - left,
                                       'end': min(end, right) - left, 'repeated_header_range': None})
                        sources.append(dict(b['source'], doc_id=artifact['manifest']['doc_id'],
                            revision=artifact['manifest']['revision'], source_block_id=b['block_id'],
                            source_bundle_id=artifact['bundle_id']))
                    chunk['block_ranges'], chunk['sources'] = ranges, sources
                path = chunk['heading_path']
                chunk['section_role'] = 'references' if any(v.strip().lower() in {'references', 'bibliography', '参考文献'} for v in path) else 'body'
                output.append(chunk)
    return output
