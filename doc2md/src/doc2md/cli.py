from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path

from .config import PROJECT_ROOT, Settings


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    # Windows default console encoding can corrupt Chinese JSON when piped.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="本地图文文档转 Markdown（Qwen3.6-Plus）")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="检查环境与配置，不联网")
    doctor.add_argument("--env-file", type=Path)
    probe = sub.add_parser("probe", help="显式调用模型识别一张合成测试图")
    probe.add_argument("--env-file", type=Path)
    parse = sub.add_parser("parse", help="处理文件或目录")
    parse.add_argument("input", type=Path)
    parse.add_argument("--output", type=Path, default=PROJECT_ROOT / "output")
    parse.add_argument("--env-file", type=Path)
    parse.add_argument("--recursive", action="store_true")
    parse.add_argument("--offline", action="store_true", help="禁止联网，未完成的图片标为 pending")
    parse.add_argument("--require-renderer", action="store_true", help="Office 缺少 LibreOffice 则失败")
    parse.add_argument("--ocr", action="store_true", help="尝试本地 Tesseract，需 chi_sim/eng 数据")
    parse.add_argument("--category", default="未分类")
    parse.add_argument("--dpi", type=int, default=144)
    parse.add_argument("--max-image-side", type=int, default=1800)
    parse.add_argument("--timeout", type=float, default=90)
    parse.add_argument("--retries", type=int, default=2)
    parse.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args(argv)
    settings = Settings.load(args.env_file)
    if args.command == "doctor":
        packages = {}
        for name in ("PyMuPDF", "Pillow", "httpx", "python-dotenv", "python-docx", "python-pptx"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = "missing"
        emit({"python": sys.version.split()[0], "executable": sys.executable, "project": str(PROJECT_ROOT),
              "packages": packages, "base_url": settings.base_url, "model": settings.model,
              "api_key_configured": bool(settings.api_key and settings.api_key != "***"),
              "libreoffice": settings.renderer(), "tesseract": settings.ocr_engine(),
              "notes": ["没有 API Key 时仍可原生解析，图像释义为 pending",
                        "缺少 LibreOffice 时 DOCX/PPTX 使用原生后备模式，DOC/PPT 不可转换"]})
        return 1 if "missing" in packages.values() else 0
    if args.command == "probe":
        from PIL import Image, ImageDraw
        from .vision import VisionClient
        with tempfile.TemporaryDirectory() as temp:
            # A connectivity probe must not succeed solely from an old disk cache.
            settings.cache_dir = Path(temp) / "probe-cache"
            path = Path(temp) / "probe.png"
            image = Image.new("RGB", (700, 220), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 55, 200, 160), outline="black", width=3)
            draw.rectangle((470, 55, 650, 160), outline="black", width=3)
            draw.text((85, 95), "INPUT A", fill="black", font_size=22)
            draw.text((520, 95), "OUTPUT B", fill="black", font_size=22)
            draw.line((200, 107, 470, 107), fill="black", width=4)
            draw.polygon([(470, 107), (450, 97), (450, 117)], fill="black")
            image.save(path)
            client = VisionClient(settings)
            result = client.analyze(path, "识别图中文字及有向关系，不猜测额外信息。")
            emit({"result": result, "stats": client.stats,
                  "note": "请人工确认模型实际识别到 INPUT A → OUTPUT B；HTTP 成功不等于视觉准确性验证通过"})
            return 0 if result["status"] == "ok" else (2 if result["status"] == "pending" else 1)
    for name in ("offline", "require_renderer", "category", "dpi", "max_image_side", "timeout", "retries", "max_tokens"):
        setattr(settings, name, getattr(args, name))
    settings.use_ocr = args.ocr
    if not 72 <= settings.dpi <= 300 or not 128 <= settings.max_image_side <= 4096:
        parser.error("dpi 应在 72～300，max-image-side 应在 128～4096")
    if not 0 <= settings.retries <= 5 or settings.timeout <= 0 or settings.max_tokens <= 0:
        parser.error("retries 应在 0～5，timeout/max-tokens 应为正数")
    from .pipeline import parse_path
    try:
        report = parse_path(args.input, args.output, settings, recursive=args.recursive)
        emit(report)
        return report["exit_code"]
    except (ValueError, OSError) as exc:
        emit({"status": "failed", "error": str(exc)})
        return 1
    except KeyboardInterrupt:
        emit({"status": "interrupted", "error": "已中断；已完成文档保留，可重新执行"})
        return 130
