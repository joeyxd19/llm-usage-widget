# -*- coding: utf-8 -*-
"""core/providers_zhipu.py 离线契约测试（unittest，可被 pytest 兼容运行）。

用按解析逻辑与字段约定推导的真实结构示例响应做 fixture，通过替换模块级
http_json 完全离线，不发真实请求、不依赖 DPAPI / Qt。

契约锚定范围（只断言现有行为，不改业务代码）：
- 正常响应的窗口映射与 MCP 汇总
- unit/number → 5 小时 / 本周窗口的显式区分与兜底排序
- 错误响应 / 网络失败 / 非 dict 响应体的错误文案
- 数值类型异常（字符串 / None）下"存储原样、计算路径异常"的现状

畸形结构（data / limits / limit item 非预期类型）期望收敛为
"响应结构异常"错误而非抛未捕获异常，见文件末尾
TestMalformedStructureConvergence；MCP usage/currentValue 为字符串
时按安全路径计算百分比（算不出则 percent=None），见 TestTypeAnomalies。
"""

import datetime
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import providers_zhipu as zp   # noqa: E402


NO_DAYS_CFG = {"show_30d": False, "show_15d": False, "show_7d": False}


def make_cfg(**overrides):
    cfg = {"api_key": "test-key", "base_url": "open.bigmodel.cn"}
    cfg.update(NO_DAYS_CFG)
    cfg.update(overrides)
    return cfg


def limit_item(**fields):
    base = {"type": "TOKENS_LIMIT", "unit": 3, "number": 5,
            "percentage": 12.5, "nextResetTime": 1735660800000}
    base.update(fields)
    return base


def zhipu_payload(**data_fields):
    data = {"level": "Pro",
            "limits": [
                limit_item(),   # 5 小时窗口 unit=3 number=5
                limit_item(unit=6, number=1, percentage=40.0,
                           nextResetTime=1736208000000),   # 本周窗口
                {"type": "TIME_LIMIT", "usage": 1000, "currentValue": 250,
                 "percentage": None, "nextResetTime": 1738368000000,
                 "usageDetails": [
                     {"modelCode": "glm-4.6", "usage": 200},
                     {"modelCode": "glm-4.5-air", "usage": 50},
                 ]},
            ]}
    data.update(data_fields)
    return {"success": True, "data": data}


class FakeHttp:
    """替换 providers_zhipu.http_json：按 URL 分发，记录全部调用。

    main_result / model_usage_result 传响应体（自动包装为 (200, body)），
    或直接传 (code, body) 二元组（模拟网络失败 code=0 等场景）。"""

    def __init__(self, main_result, model_usage_result=None):
        self.main_result = main_result
        self.model_usage_result = model_usage_result
        self.calls = []

    @staticmethod
    def _as_resp(r):
        return r if isinstance(r, tuple) and len(r) == 2 else (200, r)

    def __call__(self, url, headers=None, data=None, timeout=20):
        self.calls.append((url, headers, data))
        if "model-usage" in url:
            if isinstance(self.model_usage_result, Exception):
                raise self.model_usage_result
            return self._as_resp(self.model_usage_result)
        return self._as_resp(self.main_result)


def fetch_with(payload, cfg=None, model_usage=None):
    fake = FakeHttp(payload, model_usage)
    with mock.patch.object(zp, "http_json", fake):
        result = zp.fetch_zhipu(cfg or make_cfg())
    return result, fake


# ---------------- 正常响应 ----------------

class TestNormalResponse(unittest.TestCase):

    def test_full_payload(self):
        result, _ = fetch_with(zhipu_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["error"], "")
        self.assertEqual(result["level"], "PRO")   # 接口值统一转大写
        self.assertEqual(result["five_hour"], 12.5)
        self.assertEqual(result["weekly"], 40.0)

    def test_windows_order_five_hour_first(self):
        result, _ = fetch_with(zhipu_payload())
        self.assertEqual(result["windows"], [
            ("5小时", 12.5, 1735660800000),
            ("本周", 40.0, 1736208000000),
        ])

    def test_mcp_summary_and_details(self):
        result, _ = fetch_with(zhipu_payload())
        self.assertEqual(result["mcp"], {
            "used": 250, "total": 1000,
            # percentage 缺失时按 used/total 本地计算
            "percent": 25.0,
            "reset_ms": 1738368000000,
            "details": [("glm-4.6", 200), ("glm-4.5-air", 50)],
        })

    def test_mcp_percentage_used_as_is_when_present(self):
        p = zhipu_payload()
        p["data"]["limits"][2]["percentage"] = 33.3
        result, _ = fetch_with(p)
        self.assertEqual(result["mcp"]["percent"], 33.3)

    def test_auth_header_contract(self):
        """Authorization 直接放 API Key（不带 Bearer），UA 带版本前缀。"""
        _, fake = fetch_with(zhipu_payload(), cfg=make_cfg())
        url, headers, _ = fake.calls[0]
        self.assertTrue(url.endswith("/api/monitor/usage/quota/limit"))
        self.assertTrue(url.startswith("https://open.bigmodel.cn"))
        self.assertEqual(headers["Authorization"], "test-key")
        self.assertTrue(headers["User-Agent"].startswith("usage-widget/"))

    def test_base_url_normalization(self):
        """base_url 缺 scheme 时自动补 https://，结尾斜杠被去掉。"""
        _, fake = fetch_with(zhipu_payload(),
                             cfg=make_cfg(base_url="open.bigmodel.cn/"))
        self.assertTrue(fake.calls[0][0].startswith("https://open.bigmodel.cn/"))

    def test_recent_days_requested_when_enabled(self):
        """show_30d/15d/7d 全开时并行请求三个区间接口，区间由
        startTime/endTime 表达（无 days 参数）。"""
        mu = {"success": True,
              "data": {"totalUsage": {"totalTokensUsage": 12345,
                                      "totalModelCallCount": 77}}}
        fake = FakeHttp(zhipu_payload(), mu)
        with mock.patch.object(zp, "http_json", fake):
            result = zp.fetch_zhipu(make_cfg(show_30d=True, show_15d=True,
                                             show_7d=True))
        day_calls = [c for c in fake.calls if "model-usage" in c[0]]
        self.assertEqual(len(day_calls), 3)
        spans = set()
        for url, _, _ in day_calls:
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            start = datetime.datetime.strptime(qs["startTime"][0],
                                               "%Y-%m-%d %H:%M:%S")
            end = datetime.datetime.strptime(qs["endTime"][0],
                                             "%Y-%m-%d %H:%M:%S")
            spans.add(round((end - start).total_seconds() / 86400))
        self.assertEqual(spans, {30, 15, 7})
        self.assertEqual(result["thirty_day"], {"tokens": 12345, "calls": 77})

    def test_recent_days_silent_degrade_on_error(self):
        """区间接口失败静默降级为 None，不影响主额度。"""
        fake = FakeHttp(zhipu_payload(),
                        RuntimeError("network down"))
        with mock.patch.object(zp, "http_json", fake):
            result = zp.fetch_zhipu(make_cfg(show_30d=True))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["thirty_day"])

    def test_recent_days_disabled_not_requested(self):
        _, fake = fetch_with(zhipu_payload(), cfg=make_cfg())
        self.assertFalse(any("model-usage" in c[0] for c in fake.calls))


# ---------------- 未配置密钥 ----------------

class TestMissingKey(unittest.TestCase):

    def test_empty_key(self):
        result, fake = fetch_with(zhipu_payload(), cfg=make_cfg(api_key=""))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "未配置 API Key")
        self.assertEqual(fake.calls, [])   # 不发请求

    def test_placeholder_key(self):
        result, fake = fetch_with(
            zhipu_payload(), cfg=make_cfg(api_key="在这里填你的智谱API_Key"))
        self.assertEqual(result["error"], "未配置 API Key")
        self.assertEqual(fake.calls, [])


# ---------------- 错误响应 ----------------

class TestErrorResponses(unittest.TestCase):

    def test_known_error_code_401(self):
        result, _ = fetch_with((401, {"success": False, "code": 401,
                                      "msg": "x"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "HTTP 401 API Key 无效或已过期")

    def test_known_error_code_1001(self):
        result, _ = fetch_with((401, {"success": False, "code": 1001,
                                      "msg": "x"}))
        self.assertEqual(result["error"], "HTTP 401 缺少认证信息")

    def test_unknown_error_code_uses_msg(self):
        result, _ = fetch_with((500, {"success": False, "code": 500,
                                      "msg": "boom"}))
        self.assertEqual(result["error"], "HTTP 500 boom")

    def test_network_failure(self):
        result, _ = fetch_with((0, "网络错误: refused"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "网络错误: refused")

    def test_non_dict_body(self):
        result, _ = fetch_with("gateway timeout")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "HTTP 200 gateway timeout")

    def test_success_without_data(self):
        """success=true 但缺 data：按空数据成功处理，不报错。"""
        result, _ = fetch_with({"success": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["windows"], [])
        self.assertIsNone(result["mcp"])
        self.assertEqual(result["level"], "")


# ---------------- unit/number 窗口映射边界 ----------------

class TestWindowMapping(unittest.TestCase):

    def _limits(self, *limits):
        return fetch_with({"success": True, "data": {"limits": list(limits)}})[0]

    def test_only_five_hour_window(self):
        result = self._limits(limit_item())
        self.assertEqual(result["five_hour"], 12.5)
        self.assertIsNone(result["weekly"])
        self.assertEqual(len(result["windows"]), 1)

    def test_unit_number_exact_match_required(self):
        """number=5 但 unit≠3 不算 5 小时窗口（两个 token 时归入本周位）。"""
        result = self._limits(limit_item(unit=2, number=5, percentage=7.0),
                              limit_item(unit=6, number=1, percentage=40.0))
        self.assertIsNone(result["five_hour"])
        self.assertEqual(result["weekly"], 7.0)   # 第一个非 5h 项顶到本周位

    def test_two_tokens_without_unit_number_no_fallback(self):
        """现状记录：两个 token 都缺 unit/number 时，兜底排序不会触发
        （week 已被第一个非 5h 项占住），5 小时窗口直接缺失。"""
        a = limit_item(unit=None, number=None, percentage=10.0,
                       nextResetTime=2000)
        b = limit_item(unit=None, number=None, percentage=60.0,
                       nextResetTime=9000)
        result = self._limits(a, b)
        self.assertIsNone(result["five_hour"])
        self.assertEqual(result["weekly"], 10.0)

    def test_fallback_single_token_without_unit_number(self):
        """单个 token 缺 unit/number 时走兜底：按 nextResetTime 排序后
        rest[0] 当 5 小时窗口（此时无本周窗口）。"""
        result = self._limits(limit_item(unit=None, number=None,
                                         percentage=10.0, nextResetTime=2000))
        self.assertEqual(result["five_hour"], 10.0)
        self.assertIsNone(result["weekly"])

    def test_fallback_missing_reset_time_still_mapped(self):
        """nextResetTime 缺失的项在兜底排序中为 inf，但单项兜底仍命中。"""
        result = self._limits(limit_item(unit=None, number=None,
                                         percentage=10.0, nextResetTime=None))
        self.assertEqual(result["five_hour"], 10.0)

    def test_two_identical_five_hour_tokens(self):
        """两个 token 都是 5 小时特征时：第一个当 5 小时，本周缺失。"""
        result = self._limits(limit_item(percentage=10.0),
                              limit_item(percentage=20.0,
                                         nextResetTime=9000))
        self.assertEqual(result["five_hour"], 10.0)
        self.assertIsNone(result["weekly"])

    def test_mcp_only_no_windows(self):
        result = self._limits({"type": "TIME_LIMIT", "usage": 5,
                               "currentValue": 1, "percentage": 20.0,
                               "usageDetails": []})
        self.assertIsNone(result["five_hour"])
        self.assertIsNone(result["weekly"])
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["mcp"]["percent"], 20.0)


# ---------------- 数值类型异常 ----------------

class TestTypeAnomalies(unittest.TestCase):

    def _limits(self, *limits):
        return fetch_with({"success": True, "data": {"limits": list(limits)}})[0]

    def test_percentage_string_stored_as_is(self):
        """percentage 为字符串时原样存储，不做数值转换（现状契约）。"""
        result = self._limits(limit_item(percentage="12.5"))
        self.assertEqual(result["five_hour"], "12.5")

    def test_percentage_none_stored_as_none(self):
        result = self._limits(limit_item(percentage=None))
        self.assertIsNone(result["five_hour"])

    def test_next_reset_time_string_kept_as_is(self):
        result = self._limits(
            limit_item(nextResetTime="2026-11-02 12:00:00"))
        self.assertEqual(result["windows"][0][2], "2026-11-02 12:00:00")

    def test_mcp_usage_none_treated_as_zero(self):
        result = self._limits({"type": "TIME_LIMIT", "usage": None,
                               "currentValue": 5, "percentage": None,
                               "usageDetails": None})
        self.assertEqual(result["mcp"],
                         {"used": 5, "total": 0, "percent": None,
                          "reset_ms": None, "details": []})

    def test_mcp_details_filter_non_dict_and_default_model_code(self):
        result = self._limits({"type": "TIME_LIMIT", "usage": 10,
                               "currentValue": 3, "percentage": 30.0,
                               "usageDetails": [
                                   {"modelCode": "glm-4.6", "usage": 3},
                                   "not-a-dict",
                                   None,
                                   {"usage": 7},   # 缺 modelCode → ""
                               ]})
        self.assertEqual(result["mcp"]["details"], [("glm-4.6", 3), ("", 7)])

    def test_mcp_string_values_safe_percent(self):
        """usage/currentValue 为字符串、percentage 缺失时按安全路径
        计算（可解析则算出百分比，不抛 TypeError）。"""
        result = self._limits({"type": "TIME_LIMIT", "usage": "1000",
                               "currentValue": 250, "percentage": None,
                               "usageDetails": []})
        self.assertTrue(result["ok"])
        self.assertEqual(result["mcp"]["percent"], 25.0)
        result = self._limits({"type": "TIME_LIMIT", "usage": 1000,
                               "currentValue": "250", "percentage": None,
                               "usageDetails": []})
        self.assertTrue(result["ok"])
        self.assertEqual(result["mcp"]["percent"], 25.0)

    def test_mcp_unparseable_values_percent_none(self):
        """usage/currentValue 均不可解析为数值时收敛为 percent=None，
        不抛异常，used/total 仍原样存储。"""
        result = self._limits({"type": "TIME_LIMIT", "usage": "abc",
                               "currentValue": "xyz", "percentage": None,
                               "usageDetails": []})
        self.assertTrue(result["ok"])
        self.assertIsNone(result["mcp"]["percent"])
        self.assertEqual(result["mcp"]["used"], "xyz")
        self.assertEqual(result["mcp"]["total"], "abc")


# ---------------- 畸形结构收敛 ----------------

class TestMalformedStructureConvergence(unittest.TestCase):
    """data / limits / limit item 为非预期类型时，解析收敛为统一中文
    错误"响应结构异常"（ok=False），不得抛未捕获异常。

    这类结构在真实接口中未出现过（接口要么给 dict 要么 success=false），
    但契约测试的职责是把边界钉住：一旦有人改动解析代码，这些用例会
    明确暴露行为变化。"""

    def test_data_not_dict_converges(self):
        with mock.patch.object(zp, "http_json",
                               lambda *a, **k: (200, {"success": True,
                                                      "data": "oops"})):
            result = zp.fetch_zhipu(make_cfg())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "响应结构异常")

    def test_limits_not_list_converges(self):
        with mock.patch.object(zp, "http_json",
                               lambda *a, **k: (200, {"success": True,
                                                      "data": {"limits": 3}})):
            result = zp.fetch_zhipu(make_cfg())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "响应结构异常")

    def test_limit_item_not_dict_converges(self):
        with mock.patch.object(zp, "http_json",
                               lambda *a, **k: (200, {"success": True,
                                                      "data": {"limits": [None]}})):
            result = zp.fetch_zhipu(make_cfg())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "响应结构异常")

    def test_limits_dict_converges(self):
        """limits 为 dict（可迭代但元素非 dict）同样收敛，不抛异常。"""
        with mock.patch.object(zp, "http_json",
                               lambda *a, **k: (200, {"success": True,
                                                      "data": {"limits": {"a": 1}}})):
            result = zp.fetch_zhipu(make_cfg())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "响应结构异常")


if __name__ == "__main__":
    unittest.main()
