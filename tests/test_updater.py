# -*- coding: utf-8 -*-
"""core/updater.py 纯逻辑单测：版本解析、PS 转义、sha256 与更新包校验。

Updater 在非 Windows / 非 frozen 环境实例化安全（package_type → None），
_verify_package 直接对临时文件测三种分支。"""

import hashlib

import pytest

from core import updater


# ---------------- 版本解析与比较 ----------------

def test_parse_version():
    assert updater.parse_version("v3.2.0") == (3, 2, 0)
    assert updater.parse_version("3.2.0") == (3, 2, 0)
    assert updater.parse_version("v3.10.1") == (3, 10, 1)
    assert updater.parse_version("abc") == (0, 0, 0)
    assert updater.parse_version(None) == (0, 0, 0)


def test_version_gt():
    assert updater.version_gt((3, 2, 0), (3, 1, 9)) is True
    assert updater.version_gt((3, 2, 0), (3, 2, 0)) is False
    assert updater.version_gt((3, 2, 0), (4, 0, 0)) is False


def test_ps_escape():
    assert updater._ps_escape("it's ok") == "it''s ok"
    assert updater._ps_escape("no quotes") == "no quotes"


# ---------------- sha256 / 更新包完整性 ----------------

def test_sha256_file(tmp_path):
    f = tmp_path / "pkg.zip"
    f.write_bytes(b"payload-bytes")
    assert updater._sha256_file(str(f)) == \
        hashlib.sha256(b"payload-bytes").hexdigest()


def test_verify_package(tmp_path):
    up = updater.Updater("3.2.0")
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    name = dest.name
    url = "https://example.com/" + name

    # 无 manifest / 无 packages 段 / 无该文件条目：跳过（兼容旧版发布）
    up._verify_package(url, str(dest), None)
    up._verify_package(url, str(dest), {"version": "1.0"})
    up._verify_package(url, str(dest), {"packages": {}})

    # 哈希匹配（大小写不敏感）放行
    up._verify_package(url, str(dest), {"packages": {name: good.upper()}})

    # 不匹配 → 抛异常（发布闸门：损坏的包绝不交给安装脚本）
    with pytest.raises(ValueError):
        updater.Updater("3.2.0")._verify_package(
            url, str(dest), {"packages": {name: "0" * 64}})


# ---------------- 检查更新的网络路径与错误收敛 ----------------

def test_fetch_latest_ok(monkeypatch):
    monkeypatch.setattr(updater, "http_json", lambda *a, **k: (
        200, {"tag_name": "v3.2.1", "body": "notes",
              "assets": [{"name": "a.zip"}]}))
    up = updater.Updater("3.2.0")
    assert up._fetch_latest() == ("v3.2.1", "notes", [{"name": "a.zip"}])


def test_fetch_latest_network_fail_gives_proxy_hint(monkeypatch):
    """网络层全败：提示开系统代理（国内直连 GitHub 的主因），不吐英文堆栈。"""
    monkeypatch.setattr(updater, "http_json",
                        lambda *a, **k: (0, "网络错误: timeout"))
    with pytest.raises(RuntimeError, match="系统代理"):
        updater.Updater("3.2.0")._fetch_latest()


def test_fetch_latest_rate_limited(monkeypatch):
    monkeypatch.setattr(updater, "http_json", lambda *a, **k: (403, {}))
    with pytest.raises(RuntimeError, match="上限"):
        updater.Updater("3.2.0")._fetch_latest()


def test_fetch_latest_bad_payload(monkeypatch):
    monkeypatch.setattr(updater, "http_json",
                        lambda *a, **k: (200, "not a dict"))
    with pytest.raises(RuntimeError, match="解析"):
        updater.Updater("3.2.0")._fetch_latest()


def test_download_manifest_errors(monkeypatch):
    up = updater.Updater("3.2.0")
    monkeypatch.setattr(updater, "http_json",
                        lambda *a, **k: (0, "网络错误: timeout"))
    with pytest.raises(RuntimeError, match="系统代理"):
        up._download_manifest("http://x")
    monkeypatch.setattr(updater, "http_json",
                        lambda *a, **k: (200, {"previous": "3.2.0"}))
    assert up._download_manifest("http://x") == {"previous": "3.2.0"}
