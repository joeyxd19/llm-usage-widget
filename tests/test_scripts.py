# -*- coding: utf-8 -*-
"""scripts/generate_manifest.py 单测：manifest 生成与 --seal 封签。

sys.path 由 tests/conftest.py 挂上 scripts/，直接顶层 import。"""

import hashlib
import json

import pytest

import generate_manifest as gm
import generate_winget_manifest as gw


def test_generate_manifest(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"\x00\x01")

    m = gm.generate_manifest(str(tmp_path), "3.2.0", "3.1.0")
    assert m["version"] == "3.2.0"
    assert m["previous"] == "3.1.0"
    assert m["file_count"] == 2
    paths = [f["path"] for f in m["files"]]
    assert paths == ["a.txt", "sub/b.bin"]          # 相对路径 + 排序
    by_path = {f["path"]: f for f in m["files"]}
    assert by_path["a.txt"]["size"] == 5
    assert by_path["a.txt"]["sha256"] == \
        hashlib.sha256(b"hello").hexdigest()


def test_seal_package_hashes(tmp_path):
    pkg = tmp_path / "update.zip"
    pkg.write_bytes(b"zip-bytes")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"version": "3.2.0", "file_count": 0, "files": []}),
        encoding="utf-8")

    out = gm.seal_package_hashes(str(manifest_path), [str(pkg)])
    assert out["packages"]["update.zip"] == \
        hashlib.sha256(b"zip-bytes").hexdigest()
    # 就地写回磁盘
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["packages"] == out["packages"]


def test_seal_missing_file_raises(tmp_path):
    """缺产物直接抛异常——封签是发布闸门，缺包就让流水线失败。"""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(OSError):
        gm.seal_package_hashes(str(manifest_path),
                               [str(tmp_path / "nope.zip")])


# ---------------- winget manifest 生成 ----------------

def test_winget_manifest_main(tmp_path):
    """端到端：给 --sha256 生成三件套，校验关键字段与 winget-pkgs 目录布局。"""
    sha = "ab" * 32
    rc = gw.main(["v3.2.0", "--sha256", sha.upper(),
                  "--out-dir", str(tmp_path)])
    assert rc == 0
    base = tmp_path / "j" / gw.PACKAGE_ID / "3.2.0"
    names = sorted(p.name for p in base.iterdir())
    assert names == sorted([
        "%s.yaml" % gw.PACKAGE_ID,
        "%s.installer.yaml" % gw.PACKAGE_ID,
        "%s.locale.zh-CN.yaml" % gw.PACKAGE_ID,
    ])
    version = (base / ("%s.yaml" % gw.PACKAGE_ID)).read_text(encoding="utf-8")
    assert "PackageVersion: 3.2.0" in version
    assert "ManifestType: version" in version
    inst = (base / ("%s.installer.yaml" % gw.PACKAGE_ID)).read_text(
        encoding="utf-8")
    assert "InstallerType: inno" in inst
    assert sha.upper() in inst or sha in inst   # 大小写不敏感地写入
    assert "releases/download/v3.2.0/llm-usage-widget_v3.2.0_setup.exe" in inst
    locale = (base / ("%s.locale.zh-CN.yaml" % gw.PACKAGE_ID)).read_text(
        encoding="utf-8")
    assert "ManifestType: defaultLocale" in locale
    assert "Freeware" in locale


def test_winget_manifest_requires_sha(tmp_path, monkeypatch):
    """本地没有安装包又不给 --sha256：报错退出（SystemExit）。"""
    monkeypatch.setattr(gw, "find_local_installer", lambda v: None)
    with pytest.raises(SystemExit):
        gw.main(["3.2.0", "--out-dir", str(tmp_path)])
