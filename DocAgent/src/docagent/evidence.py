import copy
import hashlib
import re
from pathlib import Path
from .common import AgentError


def _quote_view(text, line_hyphen='keep'):
    """Normalize layout only, retaining an offset map into the exact original."""
    omitted = set()
    # PDF presentation ligatures and discretionary word hyphens are layout, not paraphrases.
    for match in re.finditer(r'(?<=[A-Za-zﬀ-ﬆ])­(?:[ \t]*\r?\n[ \t]*)?(?=[A-Za-z])', text):
        omitted.update(range(match.start(), match.end()))
    for match in re.finditer(r'(?<=[\u3400-\u9fff])[ \t]*\r?\n[ \t]*(?=[\u3400-\u9fff])', text):
        omitted.update(range(match.start(), match.end()))
    if line_hyphen != 'keep':
        for match in re.finditer(r'(?<=[A-Za-z])-[ \t]*\r?\n[ \t]*(?=[A-Za-z])', text):
            start = match.start() if line_hyphen == 'remove' else match.start() + 1
            omitted.update(range(start, match.end()))
    chars, offsets = [], []
    for i, char in enumerate(text):
        if i in omitted:
            continue
        presentation = {'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl','ﬅ':'st','ﬆ':'st'}.get(char,char)
        for unit in presentation:
            unit = ' ' if unit.isspace() else unit
            if unit == ' ' and chars and chars[-1] == ' ':
                continue
            chars.append(unit)
            offsets.append(i)
    return ''.join(chars), offsets


def match_quote(text, quote):
    """Return an original contiguous span, never a fuzzy or paraphrase match."""
    if quote in text:
        start = text.index(quote)
        return start, start + len(quote), 'exact'
    for mode in ('keep', 'join', 'remove'):
        view, offsets = _quote_view(text, mode)
        needle, _ = _quote_view(quote, mode)
        needle = needle.strip()
        if not needle:
            continue
        index = view.find(needle)
        while index >= 0:
            end = index + len(needle)
            left_boundary = index == 0 or offsets[index] != offsets[index-1]
            right_boundary = end == len(offsets) or offsets[end-1] != offsets[end]
            if left_boundary and right_boundary:
                return offsets[index], offsets[end-1] + 1, 'layout-' + mode
            index = view.find(needle,index+1)
    return None


class EvidenceLedger:
    def __init__(self, budget=24000):
        self.records, self.keys, self.used, self.budget = {}, {}, 0, budget

    def add(self, row):
        row = copy.deepcopy(row)
        key = (row['artifact_id'], row.get('chunk_id'), str(row['block_ranges']), row['text'])
        if key in self.keys:
            return self.public(self.keys[key])
        if not row.get('sources') or not row['text'].strip():
            raise AgentError('EVIDENCE', '证据缺少来源或原始文本')
        # Keep original offsets unchanged. Oversized blocks are not silently truncated.
        if len(row['text']) + self.used > self.budget:
            return None
        eid = 'E' + str(len(self.records) + 1)
        row['evidence_id'] = eid
        row.setdefault('evidence_kind', 'chunk')
        self.records[eid], self.keys[key] = row, eid
        self.used += len(row['text'])
        return self.public(eid)

    def require(self, eid):
        if eid not in self.records:
            raise AgentError('CITATION', '引用的 E 编号未在本次运行登记')
        return self.records[eid]

    def public(self, eid):
        row = self.require(eid)
        headings = row.get('heading_path', [])
        reference = any(h.strip().lower() in {'references', 'bibliography', '参考文献'} for h in headings)
        return {'evidence_id': eid, 'text': row['text'], 'type': row.get('type'), 'doc_id': row['doc_id'],
                'heading_path': headings, 'content_origin': row.get('content_origin'),
                'quality_warnings': row.get('quality_warnings', []),
                'low_information': reference or len(row['text'].strip()) < 40,
                'quote_options': self.quote_options(eid),
                'sources': [{'file': Path(s.get('original_path', '')).name, 'page_index': s.get('page_index'),
                             'figure_id': s.get('figure_id')} for s in row['sources']]}

    def quote_options(self, eid):
        text = self.require(eid)['text']
        # These are exact original spans, including native PDF line breaks.
        parts = re.split(r'(?<=[。！？])|(?<=\.)\s+(?=[A-Z])', text)
        return [p[:180] for p in parts if p.strip()][:3]

    def quote_errors(self, submission):
        errors = []
        for i, claim in enumerate(submission.claims):
            for support in claim.supports:
                row = self.records.get(support.evidence_id)
                if not row or match_quote(row['text'], support.quote) is None:
                    errors.append({'claim_index':i, 'evidence_id':support.evidence_id,
                                   'exact_quote_options':self.quote_options(support.evidence_id) if row else []})
        return errors[:12]

    def validate(self, submission):
        if submission.status == 'answered' and not submission.claims:
            raise AgentError('CITATION', 'answered 必须包含有引用的事实')
        if submission.status == 'insufficient_evidence' and not any(s.strip() for s in submission.limitations):
            raise AgentError('CITATION', '证据不足时必须说明缺口')
        citations, seen, checked = [], set(), {}
        for claim in submission.claims:
            for support in claim.supports:
                row = self.require(support.evidence_id)
                requested_quote = support.quote
                match = match_quote(row['text'], requested_quote)
                if match is None:
                    raise AgentError('CITATION', f'{support.evidence_id} 的quote与原文不匹配；只允许空白/英文断行连字符差异，请复制该证据text中的连续原句')
                start, end, method = match
                # Persist the real original characters, not the normalized model quote.
                support.quote = row['text'][start:end]
                for source in row['sources']:
                    page = source.get('page_index')
                    if page is not None and (type(page) is not int or page < 1):
                        raise AgentError('CITATION', '来源页码无效')
                    path = source.get('snapshot_path_absolute') or source.get('rendered_pdf_path_absolute')
                    if not path or not Path(path).is_file():
                        raise AgentError('CITATION', '引用快照文件不存在')
                    if source.get('snapshot_path_absolute') and source.get('revision'):
                        if path not in checked:
                            digest = hashlib.sha256()
                            with Path(path).open('rb') as stream:
                                for piece in iter(lambda: stream.read(1024 * 1024), b''):
                                    digest.update(piece)
                            checked[path] = digest.hexdigest()
                        if checked[path] != source['revision']:
                            raise AgentError('SOURCE_CORRUPT', '引用原文快照哈希与登记revision不符')
                key = (support.evidence_id, support.quote)
                if key not in seen:
                    citations.append(dict(row, quote=support.quote, submitted_quote=requested_quote,
                                          quote_span={'start': start, 'end': end}, quote_match=method))
                    seen.add(key)
        return citations
