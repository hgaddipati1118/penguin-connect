import unittest

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

import app as app_module


def _client() -> TestClient:
    inner = FastAPI()
    body = "x" * 5000

    @inner.get("/penguin-connect/conversations")
    def conversations():
        return PlainTextResponse(body)

    @inner.get("/penguin-connect/conversations/c1/attachments/0")
    def attachment():
        return PlainTextResponse(body)

    @inner.post("/penguin-connect/codex/stream")
    def codex_stream():
        return PlainTextResponse(body)

    inner.add_middleware(app_module._SelectiveGZipMiddleware)
    return TestClient(inner)


class SelectiveGZipMiddlewareTest(unittest.TestCase):
    def test_compresses_json_and_ui_routes(self):
        response = _client().get(
            "/penguin-connect/conversations",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertEqual(response.text, "x" * 5000)

    def test_skips_attachments_and_streams(self):
        client = _client()
        attachment = client.get(
            "/penguin-connect/conversations/c1/attachments/0",
            headers={"Accept-Encoding": "gzip"},
        )
        stream = client.post(
            "/penguin-connect/codex/stream",
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertIsNone(attachment.headers.get("content-encoding"))
        self.assertIsNone(stream.headers.get("content-encoding"))

    def test_app_installs_selective_gzip(self):
        installed = [middleware.cls for middleware in app_module.app.user_middleware]
        self.assertIn(app_module._SelectiveGZipMiddleware, installed)


if __name__ == "__main__":
    unittest.main()
