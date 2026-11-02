# -*- coding: utf-8 -*-
"""core/providers_volcano.py 离线契约测试（unittest，可被 pytest 兼容运行）。

火山接口采用 Volcano Signature V4（AWS SigV4 变体）签名，本文件只替换
模块级 http_json 完全离线，不校验签名的密码学正确性（那需要重放服务端
算法），只锚定请求形态与响应解析行为。不依赖网络 / DPAPI / Qt。

契约锚定范围：
- 正常响应的三档窗口（5 小时 / 本周 / 本月）与 AFPDaily 的刻意忽略
- 请求契约：POST、空 JSON 体、Authorization 前缀、x-date 头
- AK/SK 未配置 / 占位符 / 误粘贴自动拆分
- 服务端错误码 → 中文提示映射
- 数值类型异常（字符串 / None / 缺字段）下的存储行为
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import providers_volcano as pv   # noqa: E402


def make_cfg(**overrides):
    cfg = {"access_key_id": "AKLTtest123",
           "secret_access_key": "sk-test",
           "region": "cn-beijing"}
    cfg.update(overrides)
    return cfg


def vol_payload(**result_fields):
    result = {
        "PlanType": "Pro",
        "AFPFiveHour": {"Quota": 100, "Used": 30, "ResetTime": 1735660800},
        "AFPWeekly": {"Quota": 500, "Used": 120, "ResetTime": 1736208000},
        "AFPMonthly": {"Quota": 2000, "Used": 800, "ResetTime": 1738368000},
        "AFPDaily": {"Quota": 10000, "Used": 50, "ResetTime": 1735612800},
    }
    result.update(result_fields)
    return {"ResponseMetadata": {}, "Result": result}


def fetch_with(payload, cfg=None):
    """payload 传响应体（自动包装为 (200, body)）或 (code, body) 二元组。"""
    calls = []

    def fake(url, headers=None, data=None, timeout=20):
        calls.append((url, headers, data))
        resp = payload if (isinstance(payload, tuple)
                           and len(payload) == 2) else (200, payload)
        return resp

    with mock.patch.object(pv, "http_json", fake):
        result = pv.fetch_volcano(cfg or make_cfg())
    return result, calls


# ---------------- 正常响应 ----------------

class TestNormalResponse(unittest.TestCase):

    def test_full_payload_windows(self):
        result, _ = fetch_with(vol_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["error"], "")
        self.assertEqual(result["level"], "Pro")
        # 官方三档窗口顺序固定：5 小时 / 本周 / 本月
        self.assertEqual(result["windows"], [
            ("5小时", 30, 100, 1735660800),
            ("本周", 120, 500, 1736208000),
            ("本月", 800, 2000, 1738368000),
        ])

    def test_afp_daily_deliberately_ignored(self):
        """AFPDaily（固定 10000）永不构成有效约束，不进窗口列表。"""
        result, _ = fetch_with(vol_payload())
        self.assertFalse(any(w[0] == "今日" for w in result["windows"]))
        self.assertEqual(len(result["windows"]), 3)

    def test_request_contract(self):
        """POST 空 JSON 体；Authorization 为 HMAC-SHA256 Credential 形式；
        带签名所需的 x-date / x-content-sha256 头；host 含 region。"""
        result, calls = fetch_with(vol_payload())
        self.assertTrue(result["ok"])
        url, headers, data = calls[0]
        self.assertTrue(url.startswith("https://ark.cn-beijing.volcengineapi.com/"))
        self.assertIn("Action=GetAFPUsage", url)
        self.assertIn("Version=2024-01-01", url)
        self.assertIsNotNone(data)          # POST 语义由 data 非 None 决定
        self.assertEqual(data, b"{}")
        self.assertTrue(headers["Authorization"].startswith(
            "HMAC-SHA256 Credential=AKLTtest123/"))
        self.assertIn("SignedHeaders=content-type;host;x-content-sha256;x-date",
                      headers["Authorization"])
        self.assertEqual(headers["X-Content-Sha256"],
                         "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a")
        self.assertIn("T", headers["X-Date"])   # %Y%m%dT%H%M%SZ 形态
        self.assertTrue(headers["X-Date"].endswith("Z"))

    def test_region_configurable(self):
        _, calls = fetch_with(vol_payload(), cfg=make_cfg(region="cn-shanghai"))
        self.assertIn("ark.cn-shanghai.volcengineapi.com", calls[0][0])


# ---------------- 未配置密钥 / 误粘贴 ----------------

class TestCredentialHandling(unittest.TestCase):

    def test_missing_credentials(self):
        result, calls = fetch_with(vol_payload(),
                                   cfg=make_cfg(access_key_id="",
                                                secret_access_key=""))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "未配置 AK/SK")
        self.assertEqual(calls, [])

    def test_placeholder_credentials(self):
        result, calls = fetch_with(
            vol_payload(),
            cfg=make_cfg(access_key_id="在这里填火山AccessKeyID_AKLT开头"))
        self.assertEqual(result["error"], "未配置 AK/SK")
        self.assertEqual(calls, [])

    def test_mis_pasted_ak_auto_split(self):
        """整段 "AK\\nSecret Access Key:xxx" 粘进 AK 栏时自动拆分。"""
        cfg = make_cfg(access_key_id="AKLTreal\nSecret Access Key:sk-real",
                       secret_access_key="")
        result, calls = fetch_with(vol_payload(), cfg=cfg)
        self.assertTrue(result["ok"])
        auth = calls[0][1]["Authorization"]
        self.assertIn("Credential=AKLTreal/", auth)
        # 签名由 sk-real 参与：只要服务端解析响应成功即证明拆分生效（离线下
        # 由 mock 保证），此处仅锚定 AK 拆分正确


# ---------------- 服务端错误 ----------------

class TestErrorResponses(unittest.TestCase):

    def _err(self, code, message=""):
        payload = {"ResponseMetadata":
                   {"Error": {"Code": code, "Message": message}}}
        return fetch_with(payload)[0]

    def test_access_denied_hint(self):
        result = self._err("AccessDenied")
        self.assertIn("ArkFullAccess", result["error"])
        self.assertIn("ark:GetAFPUsage", result["error"])
        self.assertFalse(result["ok"])

    def test_signature_mismatch_hint(self):
        result = self._err("SignatureDoesNotMatch")
        self.assertEqual(result["error"], "Secret Access Key 不正确")

    def test_invalid_credential_hint(self):
        result = self._err("InvalidCredential")
        self.assertIn("无效或已被禁用", result["error"])

    def test_unknown_error_code_passthrough(self):
        result = self._err("QuotaExceeded", "too many requests")
        self.assertEqual(result["error"], "QuotaExceeded: too many requests")

    def test_network_failure(self):
        result, _ = fetch_with((0, "网络错误: refused"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "网络错误: refused")

    def test_non_dict_body(self):
        result, _ = fetch_with("gateway timeout")
        self.assertEqual(result["error"], "HTTP 200 gateway timeout")


# ---------------- 字段缺失 / 类型异常 ----------------

class TestMalformedResults(unittest.TestCase):

    def _windows(self, payload):
        return fetch_with(payload)[0]["windows"]

    def test_empty_result(self):
        """Result 缺失：按空数据成功处理，不报错。"""
        result, _ = fetch_with({"ResponseMetadata": {}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["level"], "")

    def test_result_not_dict_raises_documented(self):
        """现状记录：Result 为字符串等非 dict 时，解析抛未捕获
        AttributeError（res.get 调用前无类型检查）。断言的是当前事实，
        不代表期望行为；修复与否由业务决定。"""
        with self.assertRaises(AttributeError):
            fetch_with({"ResponseMetadata": {}, "Result": "oops"})

    def test_window_entry_not_dict_skipped(self):
        result = self._windows(vol_payload(AFPFiveHour="oops"))
        self.assertEqual([w[0] for w in result], ["本周", "本月"])

    def test_window_quota_falsy_skipped(self):
        result = self._windows(vol_payload(AFPWeekly={"Quota": 0,
                                                      "Used": 5}))
        self.assertEqual([w[0] for w in result], ["5小时", "本月"])

    def test_window_used_missing_defaults_zero(self):
        result = self._windows(vol_payload(AFPFiveHour={"Quota": 100}))
        self.assertEqual(result[0], ("5小时", 0, 100, 0))

    def test_reset_time_none_defaults_zero(self):
        result = self._windows(vol_payload(AFPWeekly={
            "Quota": 500, "Used": 1, "ResetTime": None}))
        self.assertEqual(result[1], ("本周", 1, 500, 0))

    def test_used_string_stored_as_is(self):
        """Used/ResetTime 为字符串时原样存储，不做数值转换（现状契约）。"""
        result = self._windows(vol_payload(AFPFiveHour={
            "Quota": 100, "Used": "30", "ResetTime": "1735660800"}))
        self.assertEqual(result[0], ("5小时", "30", 100, "1735660800"))

    def test_plan_type_missing_empty_level(self):
        result, _ = fetch_with(vol_payload(PlanType=None))
        self.assertEqual(result["level"], "")


if __name__ == "__main__":
    unittest.main()
