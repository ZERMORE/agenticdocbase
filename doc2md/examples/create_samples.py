"""Generate small, non-private fixtures for a repeatable offline demonstration."""
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw
from docx import Document
from docx.shared import Inches as WordInches
from pptx import Presentation
from pptx.util import Inches

root = Path(__file__).resolve().parent

image = Image.new("RGB", (900, 300), "white")
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((40, 95, 290, 200), radius=12, outline="#185a7a", width=4)
draw.rounded_rectangle((600, 95, 860, 200), radius=12, outline="#185a7a", width=4)
draw.text((110, 130), "INPUT A", fill="#123c50", font_size=30)
draw.text((655, 130), "OUTPUT B", fill="#123c50", font_size=30)
draw.line((290, 147, 600, 147), fill="#185a7a", width=5)
draw.polygon([(600, 147), (575, 133), (575, 161)], fill="#185a7a")
draw.text((360, 105), "TRANSFER", fill="#123c50", font_size=22)
image.save(root / "flow.png")

pdf = fitz.open()
page = pdf.new_page(width=595, height=700)
page.insert_text((45, 50), "doc2md 示例：正文、表格与流程图", fontname="china-s", fontsize=20)
page.insert_text((45, 90), "这是本地生成的测试文档，不含个人资料。", fontname="china-s", fontsize=12)
page.insert_text((45, 115), "Source fact: document version 1729. Flow direction: A to B.", fontsize=11)
for x in (45, 285, 545):
    page.draw_line((x, 150), (x, 230), color=(0.3, 0.4, 0.5))
for y in (150, 190, 230):
    page.draw_line((45, y), (545, y), color=(0.3, 0.4, 0.5))
for position, text in [((55, 175), "Item"), ((295, 175), "Value"),
                       ((55, 215), "Version"), ((295, 215), "1729")]:
    page.insert_text(position, text, fontsize=11)
page.insert_image(fitz.Rect(45, 290, 545, 457), filename=str(root / "flow.png"))
page.insert_text((45, 510), "图 1：数据从 A 发送至 B；请核验模型是否保留箭头方向。", fontname="china-s", fontsize=11)
pdf.save(root / "sample.pdf")
pdf.close()

word = Document()
word.add_heading("doc2md Word 示例", 0)
word.add_paragraph("测试事实：版本号是 1729。下面是表格与流程图。")
table = word.add_table(rows=2, cols=2)
table.cell(0, 0).text, table.cell(0, 1).text = "项目", "值"
table.cell(1, 0).text, table.cell(1, 1).text = "版本", "1729"
word.add_picture(str(root / "flow.png"), width=WordInches(5.5))
word.add_paragraph("图 1：A → B。")
word.save(root / "sample.docx")

ppt = Presentation()
slide = ppt.slides.add_slide(ppt.slide_layouts[6])
title = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(1))
title.text = "doc2md PPT 示例：A → B"
slide.shapes.add_picture(str(root / "flow.png"), Inches(0.5), Inches(2), width=Inches(9))
ppt.save(root / "sample.pptx")
print("Created sample.pdf, sample.docx, sample.pptx and flow.png")
