# -*- coding: utf-8 -*-
"""core/providers_deepseek.py 离线契约测试（unittest，可被 pytest 兼容运行）。

用官方文档（api-docs.deepseek.com/api/get-user-balance）描述的真实响应
结构做 fixture，通过替换模块级 http_json 完全离线，不发真实请求、
不依赖 DPAPI / Qt。

契约锚定范围：
- /user/balance 正常响应：单币种 / 多币种（逐条列出、不做合计）、
  is_available=false（仍 ok=True，余额可用但账户状态标注）
- 错误路径：401 / 5xx / 网络失败 / 非 dict 响应体
- 畸形结构收敛为「响应结构异常」，不抛未捕获异常
- 金额为字符串（官方口径）与非法金额
- 错误信息与结果中不得出现 API Key 明文
- balance_spent 账本模型：下降累计消耗、上涨记入账（无负消耗）、
  观测缺口标记数据不完整、样本不足保持安静
- /v1/usage 实验接口：默认关闭不请求；开启后 200 与 404 两条路径
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import providers_deepseek as dsk   # noqa: E402


KEY = "sk-test-key-0123456789abcdef"


def make_cfg(**overrides):
    cfg = {"enabled": True, "api_key": KEY, "experimental_usage": False}
    cfg.update(overrides)
    return cfg


def balance_payload(**fields):
    body = {"is_available": True,
            "balance_infos": [{"currency": "CNY",
                               "total_balance": "110.00",
                               "granted_balance": "10.00",
                               "topped_up_balance": "100.00"}]}
    body.update(fields)
    return body


class FakeHttp:
    """替换 providers_deepseek.http_json：按 URL 分发，记录全部调用。

    result 为响应体（自动包装 (200, body)）或 (code, body) 二元组；
    usage_result 同理，仅匹配 /v1/usage 请求。"""

    def __init__(self, result, usage_result=None):
        self.result = result
        self.usage_result = usage_result
        self.calls = []

    @staticmethod
    def _as_resp(r):
        return r if isinstance(r, tuple) and len(r) == 2 else (200, r)

    def __call__(self, url, headers=None, data=None, timeout=20):
        self.calls.append((url, headers, data))
        if "/v1/usage" in url:
            if isinstance(self.usage_result, Exception):
                raise self.usage_result
            return self._as_resp(self.usage_result)
        if isinstance(self.result, Exception):
            raise self.result
        return self._as_resp(self.result)


def fetch_with(payload, cfg=None, usage_result=None):
    fake = FakeHttp(payload, usage_result)
    with mock.patch.object(dsk, "http_json", fake):
        result = dsk.fetch_deepseek(cfg or make_cfg())
    return result, fake


# ---------------- 正常响应 ----------------

class TestNormalResponse(unittest.TestCase):

    def test_single_currency(self):
        result, _ = fetch_with(balance_payload())
        self.assertTrue(result["ok"])
        self.assertEqual(result["error"], "")
        self.assertEqual(len(result["balances"]), 1)
        b = result["balance"]
        self.assertEqual(b["currency"], "CNY")
        self.assertEqual(b["total"], 110.0)      # 金额字符串 → float
        self.assertEqual(b["granted"], 10.0)
        self.assertEqual(b["topped_up"], 100.0)
        self.assertTrue(b["is_available"])

    def test_multi_currency_listed_separately(self):
        p = balance_payload(balance_infos=[
            {"currency": "CNY", "total_balance": "110.00",
             "granted_balance": "10.00", "topped_up_balance": "100.00"},
            {"currency": "USD", "total_balance": "5.50",
             "granted_balance": "0.00", "topped_up_balance": "5.50"},
        ])
        result, _ = fetch_with(p)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["balances"]), 2)
        self.assertEqual(result["balances"][0]["currency"], "CNY")
        self.assertEqual(result["balances"][1]["currency"], "USD")
        self.assertEqual(result["balance"], result["balances"][0])

    def test_is_available_false_still_ok(self):
        result, _ = fetch_with(balance_payload(is_available=False))
        self.assertTrue(result["ok"])           # 数据本身有效，不算错误
        self.assertFalse(result["balance"]["is_available"])

    def test_auth_header_contract(self):
        _, fake = fetch_with(balance_payload(), cfg=make_cfg())
        url, headers, _ = fake.calls[0]
        self.assertEqual(url, dsk.BALANCE_URL)
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertTrue(headers["User-Agent"].startswith("usage-widget/"))

    def test_result_shape_matches_provider_contract(self):
        result, _ = fetch_with(balance_payload())
        for key in ("ok", "level", "windows", "error"):
            self.assertIn(key, result)


# ---------------- 错误路径 ----------------

class TestErrorResponses(unittest.TestCase):

    def test_401_invalid_key(self):
        result, _ = fetch_with((401, {"error": {"message": "auth failed"}}))
        self.assertFalse(result["ok"])
        self.assertIn("无效", result["error"])

    def test_500_with_official_message(self):
        result, _ = fetch_with(
            (500, {"error": {"message": "server busy"}}))
        self.assertFalse(result["ok"])
        self.assertIn("HTTP 500", result["error"])
        self.assertIn("server busy", result["error"])

    def test_network_failure(self):
        result, _ = fetch_with((0, "网络错误: connection refused"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "网络错误: connection refused")

    def test_non_dict_body(self):
        result, _ = fetch_with("gateway timeout html")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "响应结构异常")


# ---------------- 畸形结构收敛 ----------------

class TestMalformedStructureConvergence(unittest.TestCase):

    def test_body_is_list(self):
        result, _ = fetch_with([1, 2, 3])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "响应结构异常")

    def test_balance_infos_missing(self):
        result, _ = fetch_with({"is_available": True})
        self.assertEqual(result["error"], "响应结构异常")

    def test_balance_infos_not_list(self):
        result, _ = fetch_with(balance_payload(balance_infos={"a": 1}))
        self.assertEqual(result["error"], "响应结构异常")

    def test_balance_infos_empty(self):
        result, _ = fetch_with(balance_payload(balance_infos=[]))
        self.assertEqual(result["error"], "响应结构异常")

    def test_balance_infos_items_not_dict(self):
        result, _ = fetch_with(balance_payload(balance_infos=["CNY"]))
        self.assertEqual(result["error"], "响应结构异常")

    def test_total_balance_missing(self):
        result, _ = fetch_with(balance_payload(
            balance_infos=[{"currency": "CNY"}]))
        self.assertEqual(result["error"], "响应结构异常")

    def test_total_balance_not_number(self):
        result, _ = fetch_with(balance_payload(
            balance_infos=[{"currency": "CNY", "total_balance": "abc"}]))
        self.assertEqual(result["error"], "响应结构异常")

    def test_one_invalid_entry_skipped_valid_kept(self):
        result, _ = fetch_with(balance_payload(balance_infos=[
            {"currency": "BAD", "total_balance": None},
            {"currency": "CNY", "total_balance": "88.00"},
        ]))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["balances"]), 1)
        self.assertEqual(result["balances"][0]["total"], 88.0)

    def test_optional_money_fields_bad_converge_none(self):
        result, _ = fetch_with(balance_payload(
            balance_infos=[{"currency": "CNY", "total_balance": "1.00",
                            "granted_balance": "x", "topped_up_balance": None}]))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["balance"]["granted"])
        self.assertIsNone(result["balance"]["topped_up"])


# ---------------- 未配置 / 密钥不泄露 ----------------

class TestKeySafety(unittest.TestCase):

    def test_placeholder_key_no_request(self):
        result, fake = fetch_with(
            balance_payload(), cfg=make_cfg(api_key="在这里填你的DeepSeekAPI_Key"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "未配置 API Key")
        self.assertEqual(fake.calls, [])

    def test_empty_key_no_request(self):
        result, fake = fetch_with(balance_payload(), cfg=make_cfg(api_key="  "))
        self.assertFalse(result["ok"])
        self.assertEqual(fake.calls, [])

    def test_key_never_in_result(self):
        for payload in ((401, {}), (0, "网络错误: refused"),
                        "gateway html", [1], {"error": {"message": KEY}}):
            result, _ = fetch_with(payload)
            self.assertNotIn(
                KEY, json.dumps(result, ensure_ascii=False),
                "错误结果里出现密钥明文：%s" % repr(payload))


# ---------------- balance_spent 账本模型 ----------------

def hist_recs(pts):
    """[(相对小时, 余额), ...] → 历史快照列表（按时间无序也可）。"""
    import time
    now = time.time()
    recs = [{"ts": now - h * 3600.0, "dsb": bal, "dsc": "CNY"}
            for h, bal in pts]
    recs.reverse()   # 故意乱序，balance_spent 应自行排序
    return recs


class TestBalanceSpent(unittest.TestCase):

    def test_no_result_when_fewer_than_two_samples(self):
        self.assertIsNone(dsk.balance_spent(hist_recs([(1, 100)]), 24))
        self.assertIsNone(dsk.balance_spent([], 24))
        self.assertIsNone(dsk.balance_spent(None, 24))

    def test_decrease_is_spent(self):
        r = dsk.balance_spent(hist_recs([(23, 100), (0, 90)]), 24)
        self.assertEqual(r["spent"], 10.0)
        self.assertEqual(r["credit"], 0.0)
        self.assertEqual(r["currency"], "CNY")

    def test_increase_is_credit_never_negative_spent(self):
        """充值导致余额上涨：不得出现负消耗。"""
        r = dsk.balance_spent(hist_recs([(23, 90), (0, 110)]), 24)
        self.assertEqual(r["spent"], 0.0)
        self.assertEqual(r["credit"], 20.0)

    def test_mixed_debits_and_credits(self):
        """降 5、充 100、再降 2 → 消耗 7，入账 100（不冲减）。"""
        r = dsk.balance_spent(hist_recs(
            [(23, 100), (10, 95), (6, 195), (0, 193)]), 24)
        self.assertEqual(r["spent"], 7.0)
        self.assertEqual(r["credit"], 100.0)

    def test_gap_marks_partial(self):
        import time
        now = time.time()
        recs = [{"ts": now - 24 * 3600 - 7200, "dsb": 100.0, "dsc": "CNY"},
                {"ts": now - 3600, "dsb": 95.0, "dsc": "CNY"}]
        r = dsk.balance_spent(recs, 24 * 7)
        self.assertTrue(r["partial"])   # 相邻观测缺口 2 小时 > 10 分钟

    def test_window_start_gap_marks_partial(self):
        """最早观测距窗口起点超 10 分钟：窗口前段未观测。"""
        import time
        now = time.time()
        recs = [{"ts": now - 23 * 3600, "dsb": 100.0, "dsc": "CNY"},
                {"ts": now, "dsb": 95.0, "dsc": "CNY"}]
        r = dsk.balance_spent(recs, 24)
        self.assertTrue(r["partial"])

    def test_dense_observations_not_partial(self):
        """观测点覆盖窗口起点且相邻缺口 ≤10 分钟：不算数据不完整。"""
        import time
        now = time.time()
        # 1 小时窗口，每 10 分钟一个观测点，首点距窗口起点仅 5 分钟
        recs = [{"ts": now - 600 * i, "dsb": 100.0 - i * 0.1, "dsc": "CNY"}
                for i in range(6)]
        r = dsk.balance_spent(recs, 1)
        self.assertFalse(r["partial"])

    def test_dirty_records_ignored(self):
        import time
        now = time.time()
        recs = [{"ts": now - 7200, "dsb": 100.0, "dsc": "CNY"},
                "junk", 42, {"no_ts": 1},
                {"ts": "bad", "dsb": 99}, {"ts": now - 3600},
                {"ts": now, "dsb": 98.0, "dsc": "CNY"}]
        r = dsk.balance_spent(recs, 24)
        self.assertEqual(r["spent"], 2.0)

    def test_currency_from_latest_snapshot(self):
        import time
        now = time.time()
        recs = [{"ts": now - 7200, "dsb": 100.0, "dsc": "CNY"},
                {"ts": now, "dsb": 99.0, "dsc": "USD"}]
        r = dsk.balance_spent(recs, 24)
        self.assertEqual(r["currency"], "USD")


# ---------------- /v1/usage 实验接口 ----------------

def usage_payload(records):
    return {"data": records}


def usage_record(model="deepseek-chat", cents=123, date="2026-11-04"):
    return {"id": "r1", "model_name": model, "total_tokens": 1000,
            "prompt_tokens": 800, "input_cache_hit_tokens": 0,
            "input_cache_miss_tokens": 800, "completion_tokens": 200,
            "cost_in_cents": cents, "date": date, "request_count": 3}


class TestUsageExperimental(unittest.TestCase):

    def test_off_by_default_no_usage_request(self):
        result, fake = fetch_with(balance_payload())
        self.assertTrue(result["ok"])
        self.assertIsNone(result["usage"])
        self.assertEqual(len(fake.calls), 1)   # 只有余额请求

    def test_200_both_queries(self):
        u = usage_payload([usage_record(cents=123)])
        result, fake = fetch_with(
            balance_payload(), cfg=make_cfg(experimental_usage=True),
            usage_result=u)
        self.assertTrue(result["ok"])
        usage = result["usage"]
        self.assertEqual(usage["today_cost"], 1.23)
        self.assertEqual(usage["week_cost"], 1.23)
        self.assertEqual(usage["today_models"],
                         [("deepseek-chat", 1.23)])
        self.assertEqual(usage["currency"], "CNY")
        usage_calls = [c for c in fake.calls if "/v1/usage" in c[0]]
        self.assertEqual(len(usage_calls), 2)   # 今日 + 近 7 天两次查询
        q = usage_calls[0][0].split("?")[1]
        self.assertIn("start_date=", q) and self.assertIn("end_date=", q)

    def test_usage_404_silent_fallback(self):
        result, _ = fetch_with(
            balance_payload(), cfg=make_cfg(experimental_usage=True),
            usage_result=(404, {"error": {"message": "not found"}}))
        self.assertTrue(result["ok"])           # 余额不受影响
        self.assertIsNone(result["usage"])      # 静默回退推算

    def test_usage_network_failure_silent(self):
        result, _ = fetch_with(
            balance_payload(), cfg=make_cfg(experimental_usage=True),
            usage_result=(0, "网络错误: refused"))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["usage"])

    def test_usage_malformed_body_silent(self):
        for bad in ("html", [1], {"no_data": 1}, {"data": "x"},
                    {"data": ["bad", {"cost_in_cents": "x"}]}):
            result, _ = fetch_with(
                balance_payload(), cfg=make_cfg(experimental_usage=True),
                usage_result=bad)
            self.assertTrue(result["ok"], msg=repr(bad))
            self.assertIsNone(result["usage"], msg=repr(bad))

    def test_usage_aggregates_by_model(self):
        u = usage_payload([
            usage_record(model="deepseek-chat", cents=100),
            usage_record(model="deepseek-reasoner", cents=250),
            usage_record(model="deepseek-chat", cents=50),
            "junk",
        ])
        result, _ = fetch_with(
            balance_payload(), cfg=make_cfg(experimental_usage=True),
            usage_result=u)
        usage = result["usage"]
        self.assertEqual(usage["today_cost"], 4.0)
        self.assertEqual(usage["today_models"],
                         [("deepseek-reasoner", 2.5), ("deepseek-chat", 1.5)])

    def test_key_not_leaked_via_usage(self):
        result, _ = fetch_with(
            balance_payload(), cfg=make_cfg(experimental_usage=True),
            usage_result=usage_payload([usage_record(cents=1)]))
        self.assertNotIn(KEY, json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
