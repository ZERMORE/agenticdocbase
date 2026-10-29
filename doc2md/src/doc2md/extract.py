from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageOps

from .config import Settings
from .vision import VisionClient


def markdown_table(rows: list[list]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    cleaned = [[str(cell or "").replace("|", "\\|").replace("\n", "<br>") for cell in row] for row in rows]
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    lines = ["| " + " | ".join(row) + " |" for row in cleaned]
    lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


class Extractor:
    def __init__(self, cfg: Settings, workspace: Path, origin: Path, doc_id: str, revision: str, vision: VisionClient):
        self.cfg, self.workspace, self.origin = cfg, workspace, origin
        self.doc_id, self.revision, self.vision = doc_id, revision, vision
        self.blocks: list[dict] = []
        self.warnings: list[str] = []
        self.page_count = None
        self.mode = ""
        self.rendered_pdf: str | None = None
        (workspace / "images").mkdir(parents=True, exist_ok=True)
        self.snapshot = ""

    def add(self, kind: str, text: str, *, page=None, slide=None, anchor=None, bbox=None,
            image=None, status="ok", **extra) -> dict:
        number = len(self.blocks) + 1
        block = {
            "schema_version": "1", "doc_id": self.doc_id, "revision": self.revision,
            "block_id": f"{self.doc_id}_{self.revision[:12]}_b{number:06}",
            "type": kind, "text": text, "heading_path": [], "category": self.cfg.category,
            "source": {
                "original_path": str(self.origin), "snapshot_path": self.snapshot,
                "rendered_pdf_path": self.rendered_pdf, "page_index": page,
                "printed_page_label": None, "slide_number": slide, "anchor": anchor,
                "bbox": [round(float(v), 2) for v in bbox] if bbox is not None else None,
                "image_path": image, "figure_id": f"figure_{number:06}" if image else None,
            },
            "status": status,
        }
        block.update(extra)
        self.blocks.append(block)
        return block

    def analyze_image(self, image: Path, context: str, *, page=None, slide=None, anchor=None, bbox=None,
                      scanned=False, local_text=""):
        relative = image.relative_to(self.workspace).as_posix()
        image_block = self.add("image", "扫描页" if scanned else "文档图像", page=page, slide=slide,
                               anchor=anchor, bbox=bbox, image=relative)
        if scanned and local_text.strip():
            self.add("ocr_text", local_text.strip(), page=page, slide=slide, anchor=anchor, bbox=bbox)
        try:
            result = self.vision.analyze(image, context)
        except (OSError, ValueError, Image.DecompressionBombError):
            result = {"status": "failed", "error": "图像读取或转换失败"}
        data = result.get("data")
        if data:
            if scanned and not local_text.strip() and data["transcription"].strip():
                transcription = self.add("vision_transcription", data["transcription"].strip(), page=page, slide=slide,
                                         anchor=anchor, bbox=bbox, image=relative, model=self.cfg.model)
                transcription["source"]["figure_id"] = image_block["source"]["figure_id"]
            pieces = [data["summary"]]
            if not scanned and data["transcription"].strip():
                pieces.append("图内文字：\n" + data["transcription"])
            if data["nodes"]:
                pieces.append("节点：" + "；".join(f"{n['id']}={n['label']}" for n in data["nodes"]))
            if data["edges"]:
                pieces.append("关系：\n" + "\n".join(f"- {e['source']} → {e['target']}：{e['label']}" for e in data["edges"]))
            if data["steps"]:
                pieces.append("步骤：\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(data["steps"], 1)))
            if data["uncertainties"]:
                pieces.append("不确定项：" + "；".join(data["uncertainties"]))
            description = self.add("figure_description", "\n\n".join(p for p in pieces if p.strip()),
                                   page=page, slide=slide, anchor=anchor, bbox=bbox, image=relative,
                                   vision=data, model=self.cfg.model, cached=result.get("cached", False))
            description["source"]["figure_id"] = image_block["source"]["figure_id"]
        else:
            image_block["status"] = result["status"]
            image_block["error"] = result.get("error", "图片释义未完成")
            description = self.add("figure_description", "图像释义未完成：" + image_block["error"], page=page,
                                   slide=slide, anchor=anchor, bbox=bbox, image=relative, status=result["status"],
                                   error=image_block["error"])
            description["source"]["figure_id"] = image_block["source"]["figure_id"]

    def local_ocr(self, image: Path) -> str:
        if not self.cfg.use_ocr:
            return ""
        program = self.cfg.ocr_engine()
        if not program:
            self.warnings.append("已请求本地 OCR，但未找到 Tesseract；保留模型解析或待处理状态")
            return ""
        command = [program, str(image), "stdout", "-l", "chi_sim+eng"]
        if self.cfg.tessdata:
            command.extend(["--tessdata-dir", self.cfg.tessdata])
        try:
            completed = subprocess.run(command, capture_output=True, timeout=90)
            if completed.returncode:
                self.warnings.append("本地 OCR 失败，请检查引擎与 chi_sim/eng 语言数据")
                return ""
            return completed.stdout.decode("utf-8", errors="replace").strip()
        except (OSError, subprocess.TimeoutExpired):
            self.warnings.append("本地 OCR 无法启动或执行超时")
            return ""

    def convert_office(self, source: Path) -> Path | None:
        executable = self.cfg.renderer()
        if not executable:
            if source.suffix.lower() in {".doc", ".ppt"} or self.cfg.require_renderer:
                raise RuntimeError("未找到 LibreOffice：安装后设置 DOC2MD_SOFFICE，再处理 Office 文档")
            return None
        # Use an isolated writable profile and temporary directory; never mutate originals.
        with tempfile.TemporaryDirectory(prefix="lo-", dir=self.workspace) as tmp:
            temp = Path(tmp)
            command = [executable, f"-env:UserInstallation={(temp / 'profile').as_uri()}",
                       "--headless", "--convert-to", "pdf", "--outdir", str(temp), str(source)]
            try:
                completed = subprocess.run(command, capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("LibreOffice 转换无法启动或超时") from exc
            rendered = temp / (source.stem + ".pdf")
            if completed.returncode or not rendered.is_file() or rendered.stat().st_size == 0:
                raise RuntimeError("LibreOffice 未生成有效 PDF，请用原程序检查文档是否可打开")
            destination = self.workspace / "source" / "rendered.pdf"
            # Distinct from original snapshot even when the original stem is 'rendered'.
            destination.write_bytes(rendered.read_bytes())
            self.rendered_pdf = destination.relative_to(self.workspace).as_posix()
            return destination

    def extract(self, source: Path):
        self.snapshot = source.relative_to(self.workspace).as_posix()
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            self.mode = "pdf"
            self.rendered_pdf = self.snapshot
            self.pdf(source)
        elif suffix in {".doc", ".docx", ".ppt", ".pptx"}:
            rendered = self.convert_office(source)
            if rendered:
                self.mode = "office-rendered"
                self.pdf(rendered, slides=suffix in {".ppt", ".pptx"})
            else:
                self.mode = "office-native-fallback"
                self.warnings.append("缺少 Office 渲染器：使用原生后备提取，浮动/矢量图和版式可能不完整；页码不伪造")
                self.docx(source) if suffix == ".docx" else self.pptx(source)
        else:
            self.mode = "image"
            self.page_count = 1
            image = self.workspace / "images" / "page_0001.png"
            with Image.open(source) as raw:
                normalized = ImageOps.exif_transpose(raw).convert("RGB")
                normalized.save(image)
            self.analyze_image(image, self.origin.name, page=1, scanned=True, local_text=self.local_ocr(image))

    def pdf(self, source: Path, slides=False):
        with fitz.open(source) as document:
            if document.needs_pass:
                raise RuntimeError("PDF 已加密，需要先提供可读取的未加密副本")
            if document.page_count == 0:
                raise RuntimeError("PDF 没有页面")
            self.page_count = document.page_count
            for page in document:
                if page.number in getattr(self, 'completed_pages', set()):
                    continue
                if getattr(self, 'selected_pages', None) is not None and page.number not in self.selected_pages:
                    continue
                number = page.number + 1
                slide = number if slides else None
                raw_text = page.get_text("text", sort=True).strip()
                scanned = len(re.sub(r"\s+", "", raw_text)) < 12 and bool(page.get_images())
                if scanned:
                    image = self.render(page, page.rect, f"p{number:04}_scan.png")
                    self.analyze_image(image, self.origin.name + f" 第{number}页", page=number,
                                       slide=slide, bbox=page.rect, scanned=True, local_text=self.local_ocr(image))
                    if getattr(self, 'on_page', None): self.on_page(page.number)
                    continue
                tables = []
                try:
                    tables = list(page.find_tables().tables)
                except Exception:
                    self.warnings.append(f"第{number}页表格检测失败，保留原始文字和页面来源")
                events = []
                sizes = []
                text_blocks = page.get_text("dict", sort=True)["blocks"]
                for item in text_blocks:
                    if item["type"] != 0:
                        continue
                    spans = [span for line in item.get("lines", []) for span in line.get("spans", [])]
                    sizes.extend(span["size"] for span in spans if span.get("text", "").strip())
                median_size = sorted(sizes)[len(sizes) // 2] if sizes else 12
                for item in text_blocks:
                    if item["type"] != 0:
                        continue
                    bbox = fitz.Rect(item["bbox"])
                    if any(fitz.Rect(table.bbox).contains(bbox) for table in tables):
                        continue
                    lines = item.get("lines", [])
                    text = "\n".join("".join(s["text"] for s in line.get("spans", [])) for line in lines).strip()
                    if not text:
                        continue
                    spans = [span for line in lines for span in line.get("spans", [])]
                    heading = len(text) < 180 and max((s["size"] for s in spans), default=0) > median_size * 1.25
                    events.append((bbox.y0, bbox.x0, "text", ("heading" if heading else "paragraph", text, bbox)))
                for table in tables:
                    text = markdown_table(table.extract())
                    events.append((table.bbox[1], table.bbox[0], "text", ("table", text, table.bbox)))
                images = []
                for item in page.get_images(full=True):
                    try:
                        images.extend(page.get_image_rects(item[0]))
                    except Exception:
                        self.warnings.append(f"第{number}页有图像无法确定位置，使用页面级图像核对")
                # Rendering the complete vector envelope retains connectors between separate nodes.
                drawings = page.get_drawings()
                significant = [fitz.Rect(item["rect"]) for item in drawings if fitz.Rect(item["rect"]).get_area() > 4]
                if len(significant) >= 3:
                    envelope = fitz.Rect(significant[0])
                    for rect in significant[1:]:
                        envelope |= rect
                    if envelope.width > 50 and envelope.height > 40:
                        images.append(envelope)
                seen = set()
                for index, rect in enumerate(images):
                    rect = (fitz.Rect(rect) + (-3, -3, 3, 3)) & page.rect
                    if rect.is_empty or rect.width < 20 or rect.height < 20:
                        continue
                    coords = tuple(round(v) for v in rect)
                    if coords in seen:
                        continue
                    seen.add(coords)
                    events.append((rect.y0, rect.x0, "image", (rect, f"p{number:04}_f{index + 1:03}.png")))
                if not raw_text and not events:
                    self.add("paragraph", "[空白页]", page=number, slide=slide)
                for _, _, kind, value in sorted(events, key=lambda event: (event[0], event[1], event[2])):
                    if kind == "text":
                        block_type, text, bbox = value
                        self.add(block_type, text, page=number, slide=slide, bbox=bbox)
                    else:
                        rect, name = value
                        image = self.render(page, rect, name)
                        self.analyze_image(image, self.origin.name + "\n" + raw_text[:4000],
                                           page=number, slide=slide, bbox=rect)
                if getattr(self, 'on_page', None): self.on_page(page.number)

    def render(self, page, rect, name: str) -> Path:
        target = self.workspace / "images" / name
        page.get_pixmap(dpi=self.cfg.dpi, clip=rect, alpha=False).save(target)
        return target

    def save_embedded(self, blob: bytes, name: str) -> Path | None:
        target = self.workspace / "images" / name
        try:
            import io
            with Image.open(io.BytesIO(blob)) as image:
                ImageOps.exif_transpose(image).convert("RGB").save(target)
            return target
        except Exception:
            self.warnings.append("存在无法解码的内嵌图像/矢量对象，需要 Office 渲染模式")
            return None

    def docx(self, source: Path):
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn

        document = Document(source)
        paragraph_no = 0
        image_no = 0

        def read_paragraph(paragraph, anchor):
            nonlocal image_no
            if paragraph.text.strip():
                kind = "heading" if paragraph.style and paragraph.style.name.lower().startswith("heading") else "paragraph"
                self.add(kind, paragraph.text.strip(), anchor=anchor)
            for blip in paragraph._p.iter(qn("a:blip")):
                relation_id = blip.get(qn("r:embed"))
                if not relation_id or relation_id not in document.part.related_parts:
                    continue
                image_no += 1
                image = self.save_embedded(document.part.related_parts[relation_id].blob, f"word_f{image_no:04}.png")
                if image:
                    self.analyze_image(image, self.origin.name + "\n" + paragraph.text, anchor=anchor)
        for body_item in document.element.body:
            paragraph_no += 1
            anchor = f"body/{paragraph_no}"
            if body_item.tag == qn("w:p"):
                read_paragraph(Paragraph(body_item, document), anchor)
            elif body_item.tag == qn("w:tbl"):
                table = Table(body_item, document)
                self.add("table", markdown_table([[cell.text for cell in row.cells] for row in table.rows]), anchor=anchor)
                for row_no, row in enumerate(table.rows, 1):
                    for col_no, cell in enumerate(row.cells, 1):
                        for p_no, paragraph in enumerate(cell.paragraphs, 1):
                            # Text already in the table; collect images without duplicating cell text.
                            if list(paragraph._p.iter(qn("a:blip"))):
                                text = paragraph.text
                                before = len(self.blocks)
                                read_paragraph(paragraph, f"{anchor}/r{row_no}/c{col_no}/p{p_no}")
                                if text.strip() and len(self.blocks) > before and self.blocks[before]["type"] in {"paragraph", "heading"}:
                                    # Reassign IDs after removal to preserve deterministic ordering.
                                    self.blocks.pop(before)
        self.reindex()

    def reindex(self):
        for index, block in enumerate(self.blocks, 1):
            block["block_id"] = f"{self.doc_id}_{self.revision[:12]}_b{index:06}"

    def pptx(self, source: Path):
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        presentation = Presentation(source)
        self.page_count = len(presentation.slides)
        def shapes(items, slide_no, parent=""):
            for shape in sorted(items, key=lambda item: (item.top, item.left, item.shape_id)):
                anchor = parent + f"shape/{shape.shape_id}"
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    shapes(shape.shapes, slide_no, anchor + "/")
                elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    image = self.save_embedded(shape.image.blob, f"slide{slide_no:04}_{shape.shape_id}.png")
                    if image:
                        self.analyze_image(image, self.origin.name + f" 幻灯片 {slide_no}", slide=slide_no, anchor=anchor)
                elif shape.has_table:
                    self.add("table", markdown_table([[c.text for c in r.cells] for r in shape.table.rows]), slide=slide_no, anchor=anchor)
                elif shape.has_text_frame and shape.text.strip():
                    self.add("paragraph", shape.text.strip(), slide=slide_no, anchor=anchor)
        for index, slide in enumerate(presentation.slides, 1):
            shapes(slide.shapes, index)

    def write(self):
        blocks_path = self.workspace / "blocks.jsonl"
        blocks_path.write_text("".join(json.dumps(block, ensure_ascii=False) + "\n" for block in self.blocks), encoding="utf-8")
        from urllib.parse import quote
        def local_link(path):
            return quote(path, safe="/")
        lines = [f"# {self.origin.name}", "", f"[原文快照]({local_link(self.snapshot)})", "",
                 "> 原生提取与模型解释分开记录；来源页码指向保存的 PDF 副本。", ""]
        current = None
        headings = []
        for block in self.blocks:
            src = block["source"]
            group = (src["page_index"], src["slide_number"])
            if group != current:
                current = group
                if src["slide_number"]:
                    lines.extend([f"## 幻灯片 {src['slide_number']}", ""])
                elif src["page_index"]:
                    lines.extend([f"## 第 {src['page_index']} 页", ""])
            if block["type"] == "heading":
                headings[:] = [block["text"]]
            block["heading_path"] = list(headings)
            lines.extend([f"<a id=\"{block['block_id']}\"></a>", f"<!-- block_id: {block['block_id']} -->"])
            if block["type"] == "image":
                lines.append(f"![文档图像]({local_link(src['image_path'])})")
            elif block["type"] == "heading":
                lines.append("### " + block["text"].replace("\n", " "))
            else:
                if block["type"] == "figure_description":
                    lines.append("**图片释义（模型生成）**")
                elif block["type"] in {"vision_transcription", "ocr_text"}:
                    lines.append("**页面转录（模型/OCR）**")
                lines.append(block["text"])
            location = f"页 {src['page_index']}" if src["page_index"] else (f"幻灯片 {src['slide_number']}" if src["slide_number"] else src["anchor"] or "来源文件")
            destination = src["rendered_pdf_path"] or src["snapshot_path"]
            fragment = f"#page={src['page_index']}" if src["page_index"] and destination.endswith(".pdf") else ""
            lines.extend(["", f"> 来源：[{location}]({local_link(destination)}{fragment})；状态：{block['status']}", ""])
        # heading_path is populated during rendering; persist the final block representation.
        blocks_path.write_text("".join(json.dumps(block, ensure_ascii=False) + "\n" for block in self.blocks), encoding="utf-8")
        (self.workspace / "document.md").write_text("\n".join(lines), encoding="utf-8")
