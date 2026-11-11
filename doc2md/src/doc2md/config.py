from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".webp"}


def find_executable(explicit: str, names: list[str], candidates: list[str]) -> str | None:
    if explicit:
        located = shutil.which(explicit)
        return located or (str(Path(explicit).resolve()) if Path(explicit).is_file() else None)
    for name in names:
        if located := shutil.which(name):
            return located
    return next((p for p in candidates if Path(p).is_file()), None)


@dataclass
class Settings:
    base_url: str = "https://ai.gitee.com/v1"
    model: str = "Qwen3.6-Plus"
    api_key: str = field(default="", repr=False)
    soffice: str = ""
    tesseract: str = ""
    tessdata: str = ""
    offline: bool = False
    require_renderer: bool = False
    use_ocr: bool = False
    dpi: int = 144
    max_image_side: int = 1800
    timeout: float = 90
    retries: int = 2
    max_tokens: int = 4096
    category: str = "未分类"
    cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / ".cache" / "vision")

    @classmethod
    def load(cls, env_file: Path | None = None, **overrides) -> Settings:
        values = dotenv_values(env_file or PROJECT_ROOT / ".env")
        data = {}
        for name in ("base_url", "model", "api_key", "soffice", "tesseract", "tessdata"):
            key = "DOC2MD_" + name.upper()
            data[name] = os.environ.get(key, values.get(key) or "").strip()
        data["base_url"] = data["base_url"] or cls.base_url
        data["model"] = data["model"] or cls.model
        if (PROJECT_ROOT.parent / 'ResearchDesk' / 'bundled').is_file():
            data['cache_dir'] = Path(os.environ.get('RESEARCHDESK_DATA') or
                                     (Path(os.environ['LOCALAPPDATA']) / 'ResearchDesk' / 'Data')) / 'vision_cache'
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**data)

    def renderer(self) -> str | None:
        return find_executable(self.soffice, ["soffice.com", "soffice", "soffice.exe"], [
            r"C:\Program Files\LibreOffice\program\soffice.com",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
        ])

    def ocr_engine(self) -> str | None:
        return find_executable(self.tesseract, ["tesseract"], [r"C:\Program Files\Tesseract-OCR\tesseract.exe"])

    def fingerprint(self) -> str:
        # No secret, no transient key state. Filling in a key can complete pending output.
        names = ("base_url", "model", "offline", "require_renderer", "use_ocr", "dpi",
                 "max_image_side", "max_tokens", "category", "tessdata")
        config = {name: getattr(self, name) for name in names}
        config.update(parser_version="0.1.0", prompt_version="vision-v1",
                      renderer=self.renderer(), ocr_engine=self.ocr_engine())
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
