"""Read-only page rendering with exact text or explicitly labelled block-region highlights."""
import argparse
import hashlib
import json
from pathlib import Path
import pymupdf as fitz
from PIL import Image, ImageDraw


def locate(request, output):
    path = Path(request['path'])
    if request.get('source_hash'):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != request['source_hash']:
            raise ValueError('来源快照校验失败')
    with fitz.open(path) as pdf:
        index = request['page_index']
        if type(index) is not int or not 0 <= index < len(pdf):
            raise ValueError('页序号超出范围')
        page = pdf[index]
        quote = request.get('quote', '').strip()
        hits = page.search_for(quote) if 2 <= len(quote) <= 1200 else []
        # Native PDF coordinates are unrotated crop-relative points.
        kind, rects = 'page', []
        if hits:
            kind, rects = 'text_matches', hits
        elif request.get('bbox'):
            rect = fitz.Rect(request['bbox'])
            if not rect.is_empty and not rect.is_infinite:
                kind, rects = 'block_region', [rect]
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples).convert('RGBA')
        overlay = Image.new('RGBA', image.size)
        draw = ImageDraw.Draw(overlay)
        normalized = []
        for rect in rects:
            view = (rect * page.rotation_matrix) & page.rect
            if view.is_empty: continue
            normalized.append([view.x0/page.rect.width, view.y0/page.rect.height,
                               view.x1/page.rect.width, view.y1/page.rect.height])
            coords = [view.x0*1.5, view.y0*1.5, view.x1*1.5, view.y1*1.5]
            draw.rectangle(coords, fill=(198, 40, 40, 38), outline=(198,40,40,220), width=2)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.alpha_composite(image, overlay).convert('RGB').save(output)
        return {'page_index': index, 'display_page': index+1, 'kind': kind,
                'coordinate_space': 'rotated-crop-normalized', 'page_rotation': page.rotation,
                'cropbox': list(page.cropbox), 'rects': normalized, 'image': str(output),
                'note': {'page': '仅定位页面；无可靠文字或区域位置。',
                         'text_matches': '原生文字匹配位置；同句可能在本页出现多次。',
                         'block_region': '来源块区域；不是逐字高亮。'}[kind]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--request-file', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        import contextlib, sys
        with contextlib.redirect_stdout(sys.stderr):
            result = locate(json.loads(args.request_file.read_text(encoding='utf-8')), args.output)
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__': main()
