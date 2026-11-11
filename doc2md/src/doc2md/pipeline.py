from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, SUPPORTED
from .extract import Extractor
from .vision import VisionClient


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def safe_remove_tree(path: Path, root: Path):
    resolved, parent = path.resolve(), root.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise RuntimeError("拒绝删除工作输出目录之外的路径")
    if path.exists():
        shutil.rmtree(path)


def rename_directory(source: Path, destination: Path):
    # Windows file scanners can briefly hold a recently written file open.
    for delay in (0.05, 0.15, 0.4, None):
        try:
            source.rename(destination)
            return
        except PermissionError as exc:
            if delay is None or getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            if destination.exists() or not source.is_dir():
                raise
            time.sleep(delay)


def parse_document(source: str | Path, output: str | Path, settings: Settings | None = None,
                   *, vision: VisionClient | None = None) -> dict:
    cfg = settings or Settings.load()
    origin = Path(source).resolve()
    root = Path(output).resolve()
    if not origin.is_file():
        raise ValueError("输入文件不存在")
    if origin.suffix.lower() not in SUPPORTED:
        raise ValueError("不支持的输入格式")
    if origin.is_relative_to(root):
        raise ValueError("输入不能位于输出目录内，请选择独立输出目录")
    root.mkdir(parents=True, exist_ok=True)
    doc_id = "doc_" + hashlib.sha256(str(origin).casefold().encode()).hexdigest()[:16]
    revision = file_hash(origin)
    fingerprint = cfg.fingerprint()
    destination = root / doc_id / revision / fingerprint
    if (root / (doc_id + ".lock")).exists():
        raise ValueError("该文档存在写入锁文件，请等待或确认原进程已结束")
    if destination.exists():
        prior = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
        if prior.get('status') == 'ok':
            cached_source = (destination / prior['source_snapshot']).resolve()
            if not cached_source.is_relative_to(destination.resolve()) or not cached_source.is_file() or file_hash(cached_source) != revision:
                raise ValueError('已发布产物的来源快照损坏，拒绝复用')
            cached_blocks = [json.loads(line) for line in (destination / 'blocks.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
            if prior.get('blocks_sha256') and file_hash(destination / 'blocks.jsonl') != prior['blocks_sha256']:
                raise ValueError('已发布产物内容哈希不匹配，拒绝复用')
            if len(cached_blocks) != prior['block_count']:
                raise ValueError('已发布产物内容不完整，拒绝复用')
            return {'source': str(origin), 'status': 'ok', 'output': str(destination),
                    'markdown': str(destination / 'document.md'), 'manifest': str(destination / 'manifest.json'),
                    'warnings': prior.get('warnings', []), 'unfinished_blocks': 0, 'reused': True}
        destination = destination.with_name(fingerprint + "-" + uuid.uuid4().hex[:12])
    lock_path = root / (doc_id + ".lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"该文档正在处理，或上次进程中断留下锁文件：{lock_path}") from exc
    os.write(lock_fd, f"pid={os.getpid()}".encode())
    os.close(lock_fd)
    started = time.monotonic()
    client = vision or VisionClient(cfg)
    try:
        checkpoint = root / '.checkpoints' / (doc_id + '_' + revision + '_' + fingerprint)
        if origin.suffix.lower() == '.pdf':
            checkpoint.mkdir(parents=True, exist_ok=True)
            context = nullcontext(str(checkpoint))
        else:
            context = tempfile.TemporaryDirectory(prefix='.work-', dir=root)
        with context as temporary:
            workspace = Path(temporary)
            snapshots = workspace / "source"
            snapshots.mkdir(exist_ok=True)
            snapshot = snapshots / origin.name
            shutil.copy2(origin, snapshot)
            if file_hash(snapshot) != revision:
                raise ValueError("读取过程中输入发生变化，请文件写入完成后重试")
            extractor = Extractor(cfg, workspace, origin, doc_id, revision, client)
            state_path = workspace / 'checkpoint.json'
            completed = set()
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding='utf-8'))
                if state.get('revision') != revision or state.get('fingerprint') != fingerprint:
                    raise ValueError('断点版本不匹配，请选择新的输出目录')
                extractor.blocks = state['blocks']
                extractor.warnings = state['warnings']
                completed = set(state['completed_pages'])
            extractor.completed_pages = completed
            def save_page(number):
                completed.add(number)
                write_json(state_path, {'revision': revision, 'fingerprint': fingerprint,
                           'completed_pages': sorted(completed), 'blocks': extractor.blocks,
                           'warnings': extractor.warnings})
            extractor.on_page = save_page if origin.suffix.lower() == '.pdf' else None
            extractor.extract(snapshot)
            state_path.unlink(missing_ok=True)
            if not extractor.blocks:
                extractor.warnings.append("没有提取到有效内容，请核对空白文档或未支持的对象类型")
            extractor.write()
            unfinished = [block for block in extractor.blocks if block["status"] != "ok"]
            status = "partial" if unfinished or extractor.warnings else "ok"
            manifest = {
                "schema_version": "1", "tool_version": "0.1.0", "doc_id": doc_id, "revision": revision,
                "config_fingerprint": fingerprint, "status": status, "source_path": str(origin),
                "source_snapshot": extractor.snapshot, "rendered_pdf": extractor.rendered_pdf,
                "mode": extractor.mode, "page_count": extractor.page_count,
                "block_count": len(extractor.blocks), "unfinished_blocks": len(unfinished),
                "blocks_sha256": file_hash(workspace / 'blocks.jsonl'),
                "warnings": sorted(set(extractor.warnings)),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "model": cfg.model, "base_url": cfg.base_url,
                "offline": cfg.offline, "vision": client.stats,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            from .quality import report as quality_report
            write_json(workspace / "quality.json", quality_report(extractor.blocks, manifest))
            write_json(workspace / "manifest.json", manifest)
            events = [{"event": "warning", "message": warning} for warning in manifest["warnings"]]
            events.extend({"event": "unfinished_block", "block_id": b["block_id"], "status": b["status"],
                           "message": b.get("error", "")} for b in unfinished)
            (workspace / "parse.log.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Same-document writer lock + sibling directory swap. Restore previous output on error.
            previous = destination.with_name(destination.name + ".previous-" + uuid.uuid4().hex)
            if destination.exists():
                rename_directory(destination, previous)
            try:
                rename_directory(workspace, destination)
            except Exception:
                if previous.exists():
                    rename_directory(previous, destination)
                raise
            if previous.exists():
                safe_remove_tree(previous, root)
        return {"source": str(origin), "status": status, "output": str(destination),
                "markdown": str(destination / "document.md"), "manifest": str(destination / "manifest.json"),
                "warnings": manifest["warnings"], "unfinished_blocks": manifest["unfinished_blocks"]}
    finally:
        lock_path.unlink(missing_ok=True)


def parse_path(source: str | Path, output: str | Path, settings: Settings | None = None,
               *, recursive=False, progress=True) -> dict:
    cfg = settings or Settings.load()
    src, out = Path(source).resolve(), Path(output).resolve()
    if not src.exists():
        raise ValueError("输入路径不存在")
    if src == out or src.is_relative_to(out):
        raise ValueError("输入不能等于输出目录或位于输出目录内部")
    if src.is_file():
        candidates = [src]
    else:
        iterator = src.rglob("*") if recursive else src.glob("*")
        candidates = []
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in SUPPORTED or path.name.startswith("~$"):
                continue
            if any(part in {".venv", ".conda", ".conda-pkgs", ".cache", ".git", "__pycache__"} for part in path.relative_to(src).parts):
                continue
            if path.resolve().is_relative_to(out) or path.resolve().is_relative_to(cfg.cache_dir.resolve()):
                continue
            candidates.append(path)
        candidates.sort(key=lambda path: str(path).casefold())
    if not candidates:
        raise ValueError("目录中没有支持的文档；子目录请加 --recursive")
    results = []
    for number, path in enumerate(candidates, 1):
        if progress:
            print(f"[{number}/{len(candidates)}] {path.name}", file=sys.stderr, flush=True)
        try:
            result = parse_document(path, out, cfg)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # Custom pipeline errors are local; third-party exceptions can include document data.
            message = str(exc) if type(exc) in {ValueError, RuntimeError} else f"处理失败（{type(exc).__name__}），请检查文件可读性和依赖"
            # Defense in depth against accidental credential propagation.
            if cfg.api_key:
                message = message.replace(cfg.api_key, "[REDACTED]")
            result = {"source": str(path), "status": "failed", "error": message}
        results.append(result)
        if progress:
            print(f"  -> {result['status']}", file=sys.stderr, flush=True)
    counts = {status: sum(item["status"] == status for item in results) for status in ("ok", "partial", "failed")}
    exit_code = 1 if counts["failed"] == len(results) else (2 if counts["partial"] or counts["failed"] else 0)
    report = {"schema_version": "1", "counts": counts, "exit_code": exit_code, "results": results}
    write_json(out / "batch_report.json", report)
    return report
