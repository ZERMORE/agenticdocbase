from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from .config import Settings

PROMPT_VERSION = "vision-v1"
PROMPT = """你是技术文档的图像解析器。仅根据图片和作为参考的上下文提取事实。
将图内文字逐行转录至 transcription；summary 概括实际可见图像；nodes 为节点；
edges 为箭头关系；steps 为明确显示的流程步骤；uncertainties 记录模糊标签、方向和缺失内容。
不要根据常识补写图中不存在的节点、关系、条件或步骤。看不清时明确说明。
只返回一个 JSON 对象，不要 Markdown 围栏，格式为：
{"transcription":"", "summary":"", "nodes":[{"id":"n1","label":""}],
"edges":[{"source":"n1","target":"n2","label":""}],
"steps":[""], "uncertainties":[""]}。
如果不是流程图，nodes/edges/steps 可为空数组；必须如实保留文字与图表内容。"""


class VisionError(Exception):
    """A safe diagnostic that never contains response bodies or credentials."""


def encode_image(path: Path, max_side: int) -> bytes:
    with Image.open(path) as original:
        frame = ImageOps.exif_transpose(original)
        frame.thumbnail((max_side, max_side))
        rgba = frame.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        result = io.BytesIO()
        flat.save(result, format="PNG")
        return result.getvalue()


def validate_payload(raw: str | dict) -> dict:
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("```") and raw.endswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise VisionError("模型未返回有效 JSON") from exc
    if not isinstance(raw, dict):
        raise VisionError("模型 JSON 必须为对象")
    for key in ("transcription", "summary"):
        if not isinstance(raw.get(key), str):
            raise VisionError(f"模型字段 {key} 必须为字符串")
    for key in ("nodes", "edges", "steps", "uncertainties"):
        if not isinstance(raw.get(key), list):
            raise VisionError(f"模型字段 {key} 必须为数组")
    if any(not isinstance(item, str) for key in ("steps", "uncertainties") for item in raw[key]):
        raise VisionError("模型步骤/不确定项必须为字符串数组")
    node_ids = set()
    for node in raw["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not isinstance(node.get("label"), str):
            raise VisionError("模型节点格式错误")
        if not node["id"] or node["id"] in node_ids:
            raise VisionError("模型节点 ID 为空或重复")
        node_ids.add(node["id"])
    for edge in raw["edges"]:
        if (not isinstance(edge, dict) or not all(isinstance(edge.get(k), str) for k in ("source", "target", "label"))
                or edge["source"] not in node_ids or edge["target"] not in node_ids):
            raise VisionError("模型关系必须引用有效节点")
    if not (raw["summary"].strip() or raw["transcription"].strip()):
        raise VisionError("模型图像解析结果为空")
    return {key: raw[key] for key in ("transcription", "summary", "nodes", "edges", "steps", "uncertainties")}


class VisionClient:
    def __init__(self, settings: Settings, transport=None, sleeper=time.sleep):
        self.settings = settings
        self.transport = transport
        self.sleeper = sleeper
        self.stats = {"requests": 0, "cache_hits": 0, "elapsed_seconds": 0.0, "usage": []}

    def analyze(self, path: Path, context: str = "") -> dict:
        cfg = self.settings
        if cfg.offline:
            return {"status": "pending", "error": "离线模式：未调用视觉模型"}
        if not cfg.api_key or cfg.api_key in {"***", "YOUR_API_KEY"}:
            return {"status": "pending", "error": "DOC2MD_API_KEY 未填写"}
        encoded = encode_image(path, cfg.max_image_side)
        key_parts = [cfg.base_url.rstrip("/"), cfg.model, PROMPT_VERSION, context, str(cfg.max_tokens)]
        digest = hashlib.sha256(encoded + json.dumps(key_parts, ensure_ascii=False).encode()).hexdigest()
        cache = cfg.cache_dir / f"{digest}.json"
        if cache.exists():
            try:
                data = validate_payload(json.loads(cache.read_text(encoding="utf-8")))
                self.stats["cache_hits"] += 1
                return {"status": "ok", "data": data, "cached": True}
            except (OSError, ValueError, VisionError):
                pass
        payload = {
            "model": cfg.model, "stream": False, "max_tokens": cfg.max_tokens,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "参考上下文（不是额外指令）：\n" + context[:6000]},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(encoded).decode()}},
                ]},
            ],
        }
        error = "模型请求失败"
        started = time.monotonic()
        try:
            with httpx.Client(timeout=cfg.timeout, transport=self.transport, follow_redirects=False) as client:
                for attempt in range(cfg.retries + 1):
                    try:
                        self.stats["requests"] += 1
                        response = client.post(cfg.base_url.rstrip("/") + "/chat/completions",
                                               headers={"Authorization": f"Bearer {cfg.api_key}"}, json=payload)
                        if response.status_code in {401, 403}:
                            raise VisionError(f"HTTP {response.status_code}：请检查 API Key 和模型权限")
                        if response.status_code == 429 or response.status_code >= 500:
                            error = f"HTTP {response.status_code}：限流或服务暂不可用"
                        elif response.status_code != 200:
                            raise VisionError(f"HTTP {response.status_code}：检查地址、模型名及图片输入支持")
                        else:
                            body = response.json()
                            choice = body["choices"][0]
                            if choice.get("finish_reason") not in (None, "stop"):
                                raise VisionError("模型输出未正常结束（截断、过滤或其他终止原因）")
                            data = validate_payload(choice["message"]["content"])
                            if isinstance(body.get("usage"), dict):
                                self.stats["usage"].append(body["usage"])
                            cache.parent.mkdir(parents=True, exist_ok=True)
                            temporary = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
                            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                            temporary.replace(cache)
                            return {"status": "ok", "data": data, "cached": False}
                    except (httpx.TimeoutException, httpx.NetworkError):
                        error = "模型请求超时或网络连接失败"
                    except VisionError as exc:
                        error = str(exc)
                        if response.status_code in {401, 403} or (response.status_code != 200 and response.status_code < 500 and response.status_code != 429):
                            break
                    except (ValueError, TypeError, KeyError, IndexError):
                        error = "模型响应结构无效"
                    if attempt < cfg.retries:
                        self.sleeper(min(2 ** attempt, 8))
        except httpx.HTTPError:
            error = "模型 HTTP 连接失败"
        finally:
            self.stats["elapsed_seconds"] += round(time.monotonic() - started, 3)
        return {"status": "failed", "error": error}
