"""Offline network-safety and redirect tests.
Literal public IPs require no DNS; fake openers record requests without actual connections."""

from __future__ import annotations

import email.message
import unittest
from unittest import mock

import learnradar.net as net

ORIGIN_A = "https://93.184.216.34/v1/chat/completions"
ORIGIN_B = "https://93.184.216.35/collect"
SECRET_HEADERS = {
    "Authorization": "Bearer FAKE-TEST-KEY",
    "Content-Type": "application/json",
    "User-Agent": "learning-radar/0.1",
}
SECRET_BODY = b'{"messages":[{"role":"user","content":"FAKE PRIVATE PROFILE"}]}'


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
    """Script responses and record request methods, URLs, credentials, and bodies."""

    def __init__(self, script: list[tuple[int, dict[str, str]]]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    def open(self, request, timeout=None):  # noqa: ANN001
        self.calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "authorization": request.get_header("Authorization"),
                "body": request.data,
            }
        )
        status, headers = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        return FakeResponse(status, make_headers(**headers), b'{"ok": true}', request.full_url)


class RedirectPolicyTests(unittest.TestCase):
    def _fetch(
        self,
        location: str,
        *,
        method: str = "POST",
        allow_redirects: bool = True,
        url: str = ORIGIN_A,
    ):
        opener = RecordingOpener([(307, {"Location": location}), (200, {})])
        with mock.patch.object(net, "_OPENER", opener):
            try:
                result = net.fetch(
                    url,
                    headers=SECRET_HEADERS,
                    method=method,
                    body=SECRET_BODY if method == "POST" else None,
                    timeout=5,
                    retries=0,
                    allow_redirects=allow_redirects,
                )
                error = None
            except net.FetchError as exc:
                result, error = None, exc
        return result, error, opener.calls

    def test_cross_origin_post_with_body_is_refused(self) -> None:
        result, error, calls = self._fetch(ORIGIN_B)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("跨源重定向", str(error))
        self.assertEqual(len(calls), 1, "不允许把含档案正文的 POST 发到新主机")

    def test_https_downgrade_is_refused(self) -> None:
        result, error, calls = self._fetch("http://93.184.216.35/collect", method="GET")
        self.assertIsNone(result)
        self.assertIn("降级", str(error))
        self.assertEqual(len(calls), 1)

    def test_cross_origin_get_drops_sensitive_headers(self) -> None:
        result, error, calls = self._fetch(ORIGIN_B, method="GET")
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1]["authorization"], "跨源跳转必须剥掉凭据")

    def test_same_origin_get_keeps_headers(self) -> None:
        result, error, calls = self._fetch("https://93.184.216.34/collect", method="GET")
        self.assertIsNone(error)
        self.assertEqual(calls[1]["authorization"], "Bearer FAKE-TEST-KEY")

    def test_allow_redirects_false_refuses_even_same_origin(self) -> None:
        result, error, calls = self._fetch(
            "https://93.184.216.34/collect", method="GET", allow_redirects=False
        )
        self.assertIsNone(result)
        self.assertIn("不跟随重定向", str(error))
        self.assertEqual(len(calls), 1)

    def test_post_json_refuses_redirect_by_default(self) -> None:
        opener = RecordingOpener([(302, {"Location": ORIGIN_B})])
        with mock.patch.object(net, "_OPENER", opener):
            with self.assertRaises(net.FetchError):
                net.post_json(
                    ORIGIN_A,
                    {"model": "x"},
                    headers={"Authorization": "Bearer FAKE-TEST-KEY"},
                    timeout=5,
                )
        self.assertEqual(len(opener.calls), 1)


class PublicUrlGuardTests(unittest.TestCase):
    def test_blocks_loopback_and_metadata(self) -> None:
        for url in (
            "http://localhost/",
            "http://127.0.0.1:8000/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://metadata.google.internal/",
            "http://[::1]/",
            "file:///etc/passwd",
            "ftp://example.com/file",
        ):
            with self.subTest(url=url):
                with self.assertRaises(net.FetchError):
                    net.assert_public_url(url)

    def test_allows_literal_public_ip(self) -> None:
        url = "https://93.184.216.34/index.html"
        self.assertEqual(net.assert_public_url(url), url)


if __name__ == "__main__":
    unittest.main()
