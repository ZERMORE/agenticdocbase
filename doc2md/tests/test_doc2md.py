import io
import json
from pathlib import Path

import httpx
import pymupdf as fitz
import pytest
from PIL import Image

from doc2md.config import Settings
from doc2md.extract import markdown_table
from doc2md.pipeline import parse_document, parse_path, safe_remove_tree
from doc2md.vision import VisionClient, VisionError, validate_payload


@pytest.fixture
def cfg(tmp_path):
    return Settings(offline=True, soffice=str(tmp_path / "no-soffice"), cache_dir=tmp_path / "cache")


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "流程 图.png"
    Image.new("RGB", (300, 120), "white").save(path)
    return path


@pytest.fixture
def payload():
    return {"transcription": "A B", "summary": "A sends data to B.",
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "edges": [{"source": "a", "target": "b", "label": "data"}],
            "steps": ["A sends data"], "uncertainties": []}


def completion(payload, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 10, "total_tokens": 22}}


def read_blocks(result):
    return [json.loads(line) for line in (Path(result["output"]) / "blocks.jsonl").read_text(encoding="utf-8").splitlines()]


def make_pdf(path, *, graphics=False, scan=False):
    document = fitz.open()
    page = document.new_page(width=600, height=600)
    if scan:
        image = Image.new("RGB", (600, 600), "white")
        data = io.BytesIO()
        image.save(data, format="PNG")
        page.insert_image(page.rect, stream=data.getvalue())
    else:
        page.insert_text((50, 50), "Document test: source fact 1729", fontsize=18)
        page.insert_text((50, 90), "A description longer than twelve characters.", fontsize=11)
    if graphics:
        page.draw_rect(fitz.Rect(50, 180, 200, 260))
        page.draw_rect(fitz.Rect(350, 180, 500, 260))
        page.draw_rect(fitz.Rect(200, 205, 350, 235))
        page.insert_text((100, 220), "A")
        page.insert_text((400, 220), "B")
    document.save(path)
    document.close()


def test_pdf_native_provenance_and_stable_ids(tmp_path, cfg):
    source = tmp_path / "正文 样本.pdf"
    make_pdf(source)
    out = tmp_path / "out"
    first = parse_document(source, out, cfg)
    blocks = read_blocks(first)
    assert first["status"] == "ok"
    assert "1729" in "".join(b["text"] for b in blocks)
    assert all(b["source"]["page_index"] == 1 for b in blocks)
    assert all(b["source"]["bbox"] for b in blocks)
    second = parse_document(source, out, cfg)
    assert first["output"] == second["output"]
    assert [b["block_id"] for b in blocks] == [b["block_id"] for b in read_blocks(second)]
    assert (Path(second["output"]) / blocks[0]["source"]["snapshot_path"]).is_file()
    assert not list(out.glob("*.lock"))


def test_vector_graph_is_rendered_offline(tmp_path, cfg):
    source = tmp_path / "diagram.pdf"
    make_pdf(source, graphics=True)
    result = parse_document(source, tmp_path / "out", cfg)
    blocks = read_blocks(result)
    images = [b for b in blocks if b["type"] == "image"]
    assert result["status"] == "partial"
    assert images and images[0]["status"] == "pending"
    image = Image.open(Path(result["output"]) / images[0]["source"]["image_path"])
    assert image.width > 300 and image.height > 80


def test_scanned_pdf_pending_instead_of_empty_success(tmp_path, cfg):
    source = tmp_path / "scan.pdf"
    make_pdf(source, scan=True)
    result = parse_document(source, tmp_path / "out", cfg)
    assert result["status"] == "partial"
    assert any(b["status"] == "pending" for b in read_blocks(result))


def test_image_online_produces_transcription(tmp_path, cfg, picture, payload):
    cfg.offline = False
    cfg.api_key = "test-secret"
    client = VisionClient(cfg, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=completion(payload))))
    result = parse_document(picture, tmp_path / "out", cfg, vision=client)
    assert result["status"] == "ok"
    blocks = read_blocks(result)
    assert any(b["type"] == "vision_transcription" and b["text"] == "A B" for b in blocks)
    assert any(b.get("vision", {}).get("edges") for b in blocks)
    assert len({b["source"]["figure_id"] for b in blocks if b["source"]["image_path"]}) == 1
    assert "test-secret" not in (Path(result["manifest"])).read_text(encoding="utf-8")


def test_protocol_cache_and_context_invalidation(tmp_path, picture, payload):
    cfg = Settings(api_key="DO-NOT-LOG", cache_dir=tmp_path / "cache", retries=0)
    requests = []
    def handler(req):
        requests.append(req)
        assert str(req.url) == "https://ai.gitee.com/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer DO-NOT-LOG"
        body = json.loads(req.content)
        assert body["model"] == "Qwen3.6-Plus"
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert "response_format" not in body
        return httpx.Response(200, json=completion(payload))
    client = VisionClient(cfg, transport=httpx.MockTransport(handler))
    assert client.analyze(picture, "first")["status"] == "ok"
    assert client.analyze(picture, "first")["cached"]
    assert not client.analyze(picture, "second")["cached"]
    assert len(requests) == 2
    assert client.stats["cache_hits"] == 1
    assert "DO-NOT-LOG" not in repr(cfg)


@pytest.mark.parametrize("status,expected_requests", [(401, 1), (403, 1), (400, 1), (429, 3), (503, 1)])
def test_http_failures_do_not_cache(tmp_path, picture, status, expected_requests):
    cfg = Settings(api_key="test-secret", cache_dir=tmp_path / "cache", retries=2)
    client = VisionClient(cfg, transport=httpx.MockTransport(lambda req: httpx.Response(status, text="test-secret")), sleeper=lambda _: None)
    result = client.analyze(picture)
    assert result["status"] == "failed"
    assert client.stats["requests"] == expected_requests
    assert "test-secret" not in json.dumps(result)
    assert not list(cfg.cache_dir.glob("*.json"))


def test_retry_then_success(tmp_path, picture, payload):
    counter = []
    def handler(req):
        counter.append(1)
        return httpx.Response(429) if len(counter) == 1 else httpx.Response(200, json=completion(payload))
    client = VisionClient(Settings(api_key="test", cache_dir=tmp_path / "cache"),
                          transport=httpx.MockTransport(handler), sleeper=lambda _: None)
    assert client.analyze(picture)["status"] == "ok"
    assert len(counter) == 2


def test_timeout(tmp_path, picture):
    def handler(req):
        raise httpx.ReadTimeout("secret response", request=req)
    client = VisionClient(Settings(api_key="key", retries=1, cache_dir=tmp_path / "cache"),
                          transport=httpx.MockTransport(handler), sleeper=lambda _: None)
    result = client.analyze(picture)
    assert result["status"] == "failed"
    assert client.stats["requests"] == 1
    assert "secret response" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["json", "empty", "truncated", "bad_edge"])
def test_bad_response_is_failed(tmp_path, picture, payload, kind):
    body = completion(payload)
    if kind == "json":
        body["choices"][0]["message"]["content"] = "not json"
    if kind == "empty":
        body["choices"] = []
    if kind == "truncated":
        body["choices"][0]["finish_reason"] = "length"
    if kind == "bad_edge":
        payload["edges"][0]["target"] = "nonexistent"
        body = completion(payload)
    client = VisionClient(Settings(api_key="key", retries=0, cache_dir=tmp_path / "cache"),
                          transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    assert client.analyze(picture)["status"] == "failed"


def test_offline_and_missing_key_never_send(picture, tmp_path):
    def forbidden(req):
        pytest.fail("unexpected API request")
    for settings in (Settings(offline=True, api_key="key"), Settings(api_key="")):
        settings.cache_dir = tmp_path / "cache"
        client = VisionClient(settings, transport=httpx.MockTransport(forbidden))
        assert client.analyze(picture)["status"] == "pending"
        assert client.stats["requests"] == 0


def test_word_fallback_order_and_anchors(tmp_path, cfg, picture):
    from docx import Document
    document = Document()
    document.add_heading("Word heading", 1)
    document.add_paragraph("Before the table")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Fact"
    table.cell(1, 1).text = "1729"
    document.add_picture(str(picture))
    document.add_paragraph("After the image")
    source = tmp_path / "sample.docx"
    document.save(source)
    result = parse_document(source, tmp_path / "out", cfg)
    blocks = read_blocks(result)
    assert result["status"] == "partial"
    assert "Before" in blocks[1]["text"]
    assert blocks[2]["type"] == "table" and "1729" in blocks[2]["text"]
    assert all(b["source"]["page_index"] is None for b in blocks)
    assert all(b["source"]["anchor"] for b in blocks)
    assert len({b["block_id"] for b in blocks}) == len(blocks)


def test_ppt_fallback_slide_source(tmp_path, cfg, picture):
    from pptx import Presentation
    from pptx.util import Inches
    document = Presentation()
    for index in range(2):
        slide = document.slides.add_slide(document.slide_layouts[6])
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
        box.text = f"Slide {index + 1}"
        slide.shapes.add_picture(str(picture), Inches(1), Inches(3), width=Inches(3))
    source = tmp_path / "sample.pptx"
    document.save(source)
    result = parse_document(source, tmp_path / "out", cfg)
    assert result["status"] == "partial"
    blocks = read_blocks(result)
    assert {b["source"]["slide_number"] for b in blocks} == {1, 2}
    assert all(b["source"]["page_index"] is None for b in blocks)


def test_require_renderer_and_legacy_failure(tmp_path, cfg):
    from docx import Document
    source = tmp_path / "sample.docx"
    Document().save(source)
    cfg.require_renderer = True
    result = parse_path(source, tmp_path / "out", cfg, progress=False)
    assert result["exit_code"] == 1
    assert "LibreOffice" in result["results"][0]["error"]
    legacy = tmp_path / "old.ppt"
    legacy.write_bytes(b"not a real ppt")
    result = parse_path(legacy, tmp_path / "out", cfg, progress=False)
    assert result["counts"]["failed"] == 1


def test_batch_excludes_output_and_preserves_same_names(tmp_path, cfg):
    incoming = tmp_path / "in"
    (incoming / "a").mkdir(parents=True)
    (incoming / "b").mkdir()
    for folder in ("a", "b"):
        make_pdf(incoming / folder / "same.pdf")
    output = incoming / "output"
    first = parse_path(incoming, output, cfg, recursive=True, progress=False)
    second = parse_path(incoming, output, cfg, recursive=True, progress=False)
    assert first["counts"] == second["counts"] == {"ok": 2, "partial": 0, "failed": 0}
    assert len({r["output"] for r in first["results"]}) == 2


def test_revision_preserves_old_snapshot(tmp_path, cfg):
    source = tmp_path / "version.pdf"
    make_pdf(source)
    first = parse_document(source, tmp_path / "out", cfg)
    with fitz.open(source) as document:
        document.new_page().insert_text((50, 50), "Added second page")
        document.save(tmp_path / "changed.pdf")
    source.write_bytes((tmp_path / "changed.pdf").read_bytes())
    second = parse_document(source, tmp_path / "out", cfg)
    assert first["output"] != second["output"]
    with fitz.open(Path(first["output"]) / "source" / source.name) as previous:
        assert previous.page_count == 1


def test_lock_and_path_guards(tmp_path, cfg):
    source = tmp_path / "test.pdf"
    make_pdf(source)
    with pytest.raises(ValueError):
        parse_path(tmp_path, tmp_path, cfg)
    with pytest.raises(RuntimeError):
        safe_remove_tree(tmp_path, tmp_path)
    with pytest.raises(RuntimeError):
        safe_remove_tree(tmp_path.parent, tmp_path)
    result = parse_document(source, tmp_path / "out", cfg)
    doc_id = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))["doc_id"]
    lock = tmp_path / "out" / (doc_id + ".lock")
    lock.write_text("pid=unknown")
    with pytest.raises(ValueError, match="锁文件"):
        parse_document(source, tmp_path / "out", cfg)


def test_fenced_json_and_table_escaping(payload):
    assert validate_payload("```json\n" + json.dumps(payload) + "\n```")["summary"]
    assert "a\\|b" in markdown_table([["a|b", "x\ny"], ["value"]])
    with pytest.raises(VisionError):
        validate_payload([])


def test_config_no_key_in_fingerprint(tmp_path, monkeypatch):
    for name in ("DOC2MD_API_KEY", "DOC2MD_BASE_URL", "DOC2MD_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("DOC2MD_API_KEY=\nDOC2MD_MODEL=Qwen3.6-Plus\n", encoding="utf-8")
    cfg = Settings.load(env)
    fingerprint = cfg.fingerprint()
    monkeypatch.setenv("DOC2MD_API_KEY", "new-secret")
    with_key = Settings.load(env)
    assert with_key.api_key == "new-secret"
    assert with_key.fingerprint() == fingerprint


@pytest.mark.parametrize("literal", ["test-placeholder", '"test-placeholder"', "'test-placeholder'"])
def test_dotenv_key_quotes(tmp_path, monkeypatch, literal):
    monkeypatch.delenv("DOC2MD_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"DOC2MD_API_KEY={literal}\n", encoding="utf-8")
    assert Settings.load(env).api_key == "test-placeholder"


def test_batch_skips_conda_environment_directories(tmp_path, cfg):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    make_pdf(incoming / "source.pdf")
    for directory in (".conda", ".conda-pkgs", ".venv"):
        (incoming / directory).mkdir()
        make_pdf(incoming / directory / "package-sample.pdf")
    report = parse_path(incoming, tmp_path / "out", cfg, recursive=True, progress=False)
    assert len(report["results"]) == 1
    assert report["results"][0]["source"].endswith("source.pdf")


def test_office_converter_contract(tmp_path, cfg, monkeypatch):
    from types import SimpleNamespace
    from docx import Document
    from doc2md import extract
    source = tmp_path / "office rendering.docx"
    Document().save(source)
    monkeypatch.setattr(Settings, "renderer", lambda self: "test-soffice")
    def convert(command, **kwargs):
        assert command[0] == "test-soffice" and "--headless" in command
        assert command[1].startswith("-env:UserInstallation=file:")
        target_dir = Path(command[command.index("--outdir") + 1])
        make_pdf(target_dir / (Path(command[-1]).stem + ".pdf"))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(extract.subprocess, "run", convert)
    cfg.require_renderer = True
    result = parse_document(source, tmp_path / "out", cfg)
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert manifest["mode"] == "office-rendered"
    assert (Path(result["output"]) / "source" / "rendered.pdf").is_file()
    assert all(b["source"]["page_index"] == 1 for b in read_blocks(result))


def test_local_ocr_keeps_source_pending_without_vision(tmp_path, cfg, picture, monkeypatch):
    from types import SimpleNamespace
    from doc2md import extract
    cfg.use_ocr = True
    monkeypatch.setattr(Settings, "ocr_engine", lambda self: "test-tesseract")
    def ocr(command, **kwargs):
        assert "chi_sim+eng" in command and "stdout" in command
        return SimpleNamespace(returncode=0, stdout="OCR 测试文字".encode("utf-8"))
    monkeypatch.setattr(extract.subprocess, "run", ocr)
    result = parse_document(picture, tmp_path / "out", cfg)
    blocks = read_blocks(result)
    assert result["status"] == "partial"
    assert any(b["type"] == "ocr_text" and "测试文字" in b["text"] for b in blocks)


def test_cli_codes_and_json(tmp_path, cfg, picture):
    import subprocess
    import sys
    missing_key_env = tmp_path / ".env"
    missing_key_env.write_text("DOC2MD_API_KEY=\n", encoding="utf-8")
    process = subprocess.run([sys.executable, "-m", "doc2md", "parse", str(picture), "--offline",
                              "--output", str(tmp_path / "out"), "--env-file", str(missing_key_env)],
                             capture_output=True)
    assert process.returncode == 2
    assert json.loads(process.stdout.decode("utf-8"))["counts"]["partial"] == 1
    assert "[1/1]" in process.stderr.decode("utf-8")
