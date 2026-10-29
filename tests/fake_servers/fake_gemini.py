"""最小 Gemini generateContent 假服务端，只为端到端验证 seed-image 的调用路径。仅测试用。

约定（靠模型名 / 提示词触发各种分支）：
- 请求头 x-goog-api-key 必须是 test-key，否则 403
- 模型名含 needs-text：只给 [IMAGE] 时回 400 modality 错，要求 [TEXT, IMAGE]
- 提示词以 blocked 开头：promptFeedback.blockReason=SAFETY，无图
- 提示词以 chatty 开头：只回文字不给图
- 路径不以 /v1beta/models/ 开头：回一段 HTML（模拟中转站 base_url 写错）
"""

from __future__ import annotations

import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fake_comfy import PNG


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload, content_type: str = "application/json"):
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        match = re.fullmatch(r"/v1beta/models/([^/:]+):generateContent", self.path)
        if not match:
            return self._send(200, "<!doctype html><html>home page</html>", "text/html")
        if self.headers.get("x-goog-api-key") != "test-key":
            return self._send(403, {"error": {"code": 403, "message": "API key not valid", "status": "PERMISSION_DENIED"}})
        model = match.group(1)
        body = json.loads(raw)
        parts = body["contents"][0]["parts"]
        prompt = parts[-1]["text"]
        modalities = body["generationConfig"]["responseModalities"]
        print("REQ", model, modalities, f"{len(parts) - 1} images", body["generationConfig"].get("imageConfig"), flush=True)

        if "needs-text" in model and modalities == ["IMAGE"]:
            return self._send(400, {"error": {"code": 400, "message": "Model does not support the requested response modalities: image", "status": "INVALID_ARGUMENT"}})
        if prompt.startswith("blocked"):
            return self._send(200, {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []})
        if prompt.startswith("chatty"):
            return self._send(200, {"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [{"text": "我不能画这个"}]}}]})
        return self._send(200, {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [
                    {"text": "好的，这是图片"},
                    {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}},
                ]},
            }],
            "usageMetadata": {"totalTokenCount": 42},
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8201), Handler).serve_forever()
