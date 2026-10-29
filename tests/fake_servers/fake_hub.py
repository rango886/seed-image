"""最小 OpenAI 兼容图片服务端，只为端到端验证 seed-image 的调用路径。仅测试用。"""

from __future__ import annotations

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fake_comfy import PNG


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw)
        print("REQ", self.path, {k: (f"<{len(v)} images>" if k == "images" else v) for k, v in body.items()}, flush=True)
        if body.get("prompt", "").startswith("boom"):
            return self._send(400, {"error": {"message": "参数不对：size 不支持", "type": "invalid_request_error"}})
        n = int(body.get("n", 1))
        return self._send(200, {
            "created": 0,
            "data": [{"b64_json": base64.b64encode(PNG).decode()} for _ in range(n)],
            "output_format": "png",
            "quality": "high",
            "size": "1024x1024",
            "usage": {"total_tokens": 10},
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8200), Handler).serve_forever()
