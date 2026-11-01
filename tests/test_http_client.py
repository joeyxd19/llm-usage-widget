# -*- coding: utf-8 -*-
"""core/http_client.py 纯逻辑单测：单路请求三元组与退避参数。

用假 opener/响应对象模拟网络层，不发真实请求；http_json 的重试主循环
会真 sleep（最长 14 秒），不在单测覆盖范围。"""

import io
import urllib.error

import pytest

from core import http_client as hc


# ---------------- 退避参数锚定（防被无意改动） ----------------

def test_retry_constants():
    assert hc._BACKOFFS == (2.0, 4.0, 8.0)
    assert hc._RETRYABLE == (429, 503)
    assert hc._RETRY_AFTER_CAP == 60.0


# ---------------- Retry-After 解析 ----------------

class _Err:
    """鸭子类型：只需 .headers.get。"""

    def __init__(self, headers):
        self.headers = headers


def test_retry_after_of():
    assert hc._retry_after_of(_Err({"Retry-After": "7"})) == 7.0
    assert hc._retry_after_of(_Err({"Retry-After": "120"})) == 60.0   # 封顶
    assert hc._retry_after_of(_Err({"Retry-After": "-3"})) == 0.0
    assert hc._retry_after_of(_Err({"Retry-After": "abc"})) == 0.0
    assert hc._retry_after_of(_Err({})) == 0.0


# ---------------- 假 opener / 响应 ----------------

class _FakeResp:
    def __init__(self, payload=b"", status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    def open(self, req, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._resp


# ---------------- 单路请求 ----------------

def test_once_json_ok():
    code, parsed, ra = hc._http_json_once(
        _FakeOpener(_FakeResp(b'{"ok": 1}')), "http://x", None, None, 5)
    assert (code, parsed, ra) == (200, {"ok": 1}, 0.0)


def test_once_raw_fallback():
    """响应不是合法 JSON 时回退为原文返回，不让解析异常炸出去。"""
    code, parsed, ra = hc._http_json_once(
        _FakeOpener(_FakeResp(b"plain text")), "http://x", None, None, 5)
    assert code == 200
    assert parsed == "plain text"
    assert ra == 0.0


def test_once_network_error():
    code, msg, ra = hc._http_json_once(
        _FakeOpener(exc=urllib.error.URLError("refused")),
        "http://x", None, None, 5)
    assert code == 0
    assert ra == 0.0
    assert "网络错误" in msg


def test_once_retryable_http_error_reads_retry_after():
    err = urllib.error.HTTPError("http://x", 429, "Too Many Requests",
                                 {"Retry-After": "7"}, io.BytesIO(b"limit"))
    code, parsed, ra = hc._http_json_once(
        _FakeOpener(exc=err), "http://x", None, None, 5)
    assert code == 429
    assert ra == 7.0          # 429 时把 Retry-After 带回给退避循环
    assert parsed == "limit"  # 错误响应体仍回传（便于日志定位）


def test_once_plain_http_error_no_retry_after():
    err = urllib.error.HTTPError("http://x", 401, "Unauthorized",
                                 {}, io.BytesIO(b"no"))
    code, parsed, ra = hc._http_json_once(
        _FakeOpener(exc=err), "http://x", None, None, 5)
    assert (code, parsed, ra) == (401, "no", 0.0)


# ---------------- open_url：大文件流式下载的双路回退 ----------------

def test_open_url_falls_back_to_proxy(monkeypatch):
    """直连网络层失败时回退系统代理路。"""
    direct = _FakeOpener(exc=urllib.error.URLError("refused"))
    proxy = _FakeOpener(_FakeResp(b"data"))
    monkeypatch.setattr(hc, "_openers", lambda: (direct, proxy))
    with hc.open_url("http://x") as resp:
        assert resp.read() == b"data"


def test_open_url_http_error_no_fallback(monkeypatch):
    """404/403 等服务端响应不换路重试，直接抛出。"""
    err = urllib.error.HTTPError("http://x", 403, "Forbidden",
                                 {}, io.BytesIO(b"no"))
    direct = _FakeOpener(exc=err)
    proxy = _FakeOpener(_FakeResp(b"data"))
    monkeypatch.setattr(hc, "_openers", lambda: (direct, proxy))
    with pytest.raises(urllib.error.HTTPError):
        hc.open_url("http://x")


def test_open_url_all_fail_raises_last(monkeypatch):
    """两条路都在网络层失败时抛最后一个异常。"""
    def opener_with(msg):
        return _FakeOpener(exc=urllib.error.URLError(msg))
    monkeypatch.setattr(
        hc, "_openers",
        lambda: (opener_with("first"), opener_with("second")))
    with pytest.raises(urllib.error.URLError, match="second"):
        hc.open_url("http://x")
