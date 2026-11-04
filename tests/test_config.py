# -*- coding: utf-8 -*-
"""core/config.py 纯逻辑单测：脏值收敛、深合并、密钥加解密与增量保存。

不依赖 Qt 与真实 DPAPI——加解密用确定性假实现（is_encrypted/encrypt/
decrypt 打进 core.config 命名空间），保证 Linux CI 可跑。"""

import json

import pytest

import core.config as cc


# ---------------- 假 DPAPI（加密带 [] 包裹、解密去包裹，两者不对称，
# ---------------- 借此区分"沿用旧密文"与"重新加密"） ----------------

def _fake_is_encrypted(v):
    return isinstance(v, str) and v.startswith("dpapi:")


def _fake_encrypt(v):
    return "dpapi:[%s]" % v


def _fake_decrypt(v):
    return v[len("dpapi:"):].strip("[]")


def _install_fake_crypto(monkeypatch):
    monkeypatch.setattr(cc, "is_encrypted", _fake_is_encrypted)
    monkeypatch.setattr(cc, "encrypt_secret", _fake_encrypt)
    monkeypatch.setattr(cc, "decrypt_secret", _fake_decrypt)


# ---------------- 脏值收敛 ----------------

def test_to_int():
    f = cc._to_int
    assert f(5, 1) == 5
    assert f("5", 1) == 5
    assert f("5.7", 1) == 5
    assert f("abc", 1) == 1
    assert f(None, 1) == 1
    assert f([], 1) == 1
    assert f(3, 1, lo=5) == 5
    assert f(99, 1, hi=50) == 50


def test_to_bool():
    f = cc._to_bool
    assert f(True) is True
    assert f(False) is False
    assert f("TRUE") is True
    assert f("on") is True
    assert f("yes") is True
    assert f("0") is False
    assert f("false") is False
    assert f("") is False
    assert f(0) is False
    assert f(2) is True
    # 识别不了的收敛到 default
    assert f("whatever") is False
    assert f("whatever", True) is True


def test_is_placeholder():
    assert cc.is_placeholder("") is True
    assert cc.is_placeholder(None) is True
    assert cc.is_placeholder("在这里填你的智谱API_Key") is True
    assert cc.is_placeholder("   ") is True
    assert cc.is_placeholder("sk-real-key") is False


def test_deep_merge_nested():
    dst = {"a": {"x": 1, "y": 2}, "b": 1}
    out = cc.deep_merge(dst, {"a": {"y": 3, "z": 4}, "c": 5})
    assert out == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1, "c": 5}


# ---------------- 密钥加解密 ----------------

def test_decode_secrets(monkeypatch):
    _install_fake_crypto(monkeypatch)
    cfg = {"zhipu": {"api_key": "dpapi:[sk-1]"},
           "volcano": {"secret_access_key": "plain-ak"}}
    out = cc._decode_secrets(cfg)
    assert out["zhipu"]["api_key"] == "sk-1"
    assert out["volcano"]["secret_access_key"] == "plain-ak"


def test_decode_secrets_missing_section(monkeypatch):
    _install_fake_crypto(monkeypatch)
    cfg = {}
    assert cc._decode_secrets(cfg) == {}


def test_encode_secrets(monkeypatch):
    _install_fake_crypto(monkeypatch)
    cfg = {"zhipu": {"api_key": "sk-2"},
           "volcano": {"secret_access_key": "在这里填火山SecretAccessKey"}}
    cc._encode_secrets(cfg)
    assert cfg["zhipu"]["api_key"] == "dpapi:[sk-2]"       # 明文 → 密文
    # 占位符保持原样，绝不加密
    assert cfg["volcano"]["secret_access_key"] == "在这里填火山SecretAccessKey"


def test_encode_secrets_reuses_disk_cipher(monkeypatch):
    """DPAPI 不幂等：磁盘里已有同值密文时直接沿用，不重新加密。"""
    _install_fake_crypto(monkeypatch)
    cfg = {"zhipu": {"api_key": "sk-3"}}
    disk = {"zhipu": {"api_key": "dpapi:sk-3"}}   # 旧密文（无 [] 包裹）
    cc._encode_secrets(cfg, disk)
    assert cfg["zhipu"]["api_key"] == "dpapi:sk-3"   # 沿用，而非 "dpapi:[sk-3]"


def test_encode_secrets_keeps_cipher(monkeypatch):
    _install_fake_crypto(monkeypatch)
    cfg = {"zhipu": {"api_key": "dpapi:sk-4"}}
    cc._encode_secrets(cfg)
    assert cfg["zhipu"]["api_key"] == "dpapi:sk-4"   # 已是密文，原样保留


# ---------------- 读写盘 ----------------

def test_load_config_merges_and_decrypts(monkeypatch, tmp_path):
    _install_fake_crypto(monkeypatch)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "refresh_minutes": 7,
        "zhipu": {"api_key": "dpapi:[sk-9]"},
    }), encoding="utf-8")
    monkeypatch.setattr(cc, "config_path", lambda: str(cfg_file))
    cfg = cc.load_config()
    assert cfg["refresh_minutes"] == 7          # 用户值覆盖默认
    assert cfg["zhipu"]["api_key"] == "sk-9"    # 密文解密为明文
    assert cfg["zhipu"]["enabled"] is True      # 未覆盖的键保留默认


def test_load_config_missing_file(monkeypatch, tmp_path):
    _install_fake_crypto(monkeypatch)
    monkeypatch.setattr(cc, "config_path",
                        lambda: str(tmp_path / "nope.json"))
    cfg = cc.load_config()
    assert cfg["refresh_minutes"] == cc.DEFAULT_CONFIG["refresh_minutes"]


def test_save_config_patch_encrypt_and_reuse(monkeypatch, tmp_path):
    _install_fake_crypto(monkeypatch)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(
        {"zhipu": {"api_key": "在这里填你的智谱API_Key"}}), encoding="utf-8")
    monkeypatch.setattr(cc, "config_path", lambda: str(cfg_file))

    assert cc.save_config_patch({"zhipu": {"api_key": "sk-secret"}}) is True
    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["zhipu"]["api_key"] == "dpapi:[sk-secret]"   # 落盘前加密

    # 第二次只改别的字段：旧密文原样沿用（不被重新加密覆盖）
    assert cc.save_config_patch({"refresh_minutes": 3}) is True
    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["zhipu"]["api_key"] == "dpapi:[sk-secret]"
    assert on_disk["refresh_minutes"] == 3


def test_save_config_patch_placeholder_stays_plain(monkeypatch, tmp_path):
    _install_fake_crypto(monkeypatch)
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(cc, "config_path", lambda: str(cfg_file))
    assert cc.save_config_patch(
        {"zhipu": {"api_key": "在这里填你的智谱API_Key"}}) is True
    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["zhipu"]["api_key"] == "在这里填你的智谱API_Key"


def test_version_pin():
    assert cc.APP_VERSION == "3.3.1"
    assert cc.SECRET_FIELDS == (("zhipu", "api_key"),
                                ("volcano", "secret_access_key"),
                                ("deepseek", "api_key"))
