"""模型适配层的重定向策略测试（离线，假 opener）。"""

from __future__ import annotations

import email.message
import os
import unittest
from unittest import mock

import learnradar.net as net
from learnradar.config import CandidateModel
from learnradar.models import ChatCompletionsAdapter, ModelError

CHAT_URL = "https://93.184.216.34/v1/chat/completions"


def make_headers(**kwargs) -> email.message.Message:
    message = email.message.Message()
    for key, value in kwargs.items():
        message[key.replace("_", "-")] = value
    return message


class FakeResponse:
    def __init__(self, status: int, headers: email.message.Message, body: bytes, url: str) -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self._url = url

    def read(self, size: int = -1) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class RecordingOpener:
    def __init__(self, script: list[tuple[int, dict[str, str], bytes]]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    def open(self, request, timeout=None):  # noqa: ANN001
        self.calls.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "body": request.data,
            }
        )
        status, headers, body = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        return FakeResponse(status, make_headers(**headers), body, request.full_url)


def candidate(**overrides) -> CandidateModel:
    base = dict(
        name="unit",
        label="unit",
        provider="chat_completions",
        model_id="unit-model",
        base_url="https://93.184.216.34/v1",
        api_key_env="LEARNRADAR_TEST_KEY",
        json_mode=False,
        timeout_seconds=5,
    )
    base.update(overrides)
    return CandidateModel(**base)


class AdapterRedirectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = mock.patch.dict(os.environ, {"LEARNRADAR_TEST_KEY": "FAKE-TEST-KEY"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_model_request_refuses_redirect_by_default(self) -> None:
        opener = RecordingOpener([(302, {"Location": "https://93.184.216.34/v1/other"}, b"")])
        with mock.patch.object(net, "_OPENER", opener):
            adapter = ChatCompletionsAdapter(candidate())
            with self.assertRaises(ModelError) as ctx:
                adapter.complete(system="s", user="u")
        self.assertIn("不跟随重定向", str(ctx.exception))
        self.assertEqual(len(opener.calls), 1)

    def test_model_request_with_redirect_enabled_still_refuses_cross_origin(self) -> None:
        payload = b'{"choices":[{"message":{"content":"{}"}}],"usage":{}}'
        opener = RecordingOpener(
            [(307, {"Location": "https://93.184.216.35/v1/chat/completions"}, b""), (200, {}, payload)]
        )
        with mock.patch.object(net, "_OPENER", opener):
            adapter = ChatCompletionsAdapter(candidate(follow_redirects=True))
            with self.assertRaises(ModelError) as ctx:
                adapter.complete(system="s", user="u")
        self.assertIn("跨源重定向", str(ctx.exception))
        self.assertEqual(len(opener.calls), 1, "模型请求正文不能发往新主机")

    def test_model_request_can_follow_same_origin_redirect_when_enabled(self) -> None:
        payload = (
            b'{"choices":[{"message":{"content":"{\\"ok\\": true}"}}],'
            b'"usage":{"prompt_tokens":1,"completion_tokens":1}}'
        )
        opener = RecordingOpener(
            [(302, {"Location": "https://93.184.216.34/v1/other"}, b""), (200, {}, payload)]
        )
        with mock.patch.object(net, "_OPENER", opener):
            adapter = ChatCompletionsAdapter(candidate(follow_redirects=True))
            response = adapter.complete(system="s", user="u")
        self.assertEqual(response.text, '{"ok": true}')
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(opener.calls[1]["authorization"], "Bearer FAKE-TEST-KEY")


if __name__ == "__main__":
    unittest.main()
