# -*- coding: utf-8 -*-
"""v3.2.3 加固项单测（unittest，无 Qt / pytest 依赖）：

- core/refresh_status.py：失败可见化的纯逻辑（连续失败计数、
  「上次成功刷新于 N 分钟前」文案）
- core/http_client.py 请求头 ValueError 分支：错误信息脱敏，
  固定文案、不携带异常原文（原文可能包含密钥内容）
"""

import sys
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import http_client as hc          # noqa: E402
from core.refresh_status import (FAIL_REMIND_STREAK,   # noqa: E402
                                 FailStreak, stale_suffix)


# ---------------- FailStreak：连续失败计数 ----------------

class TestFailStreak(unittest.TestCase):

    def test_remind_exactly_once_at_streak(self):
        """连续第 3 轮失败提醒一次，之后的持续失败不再提醒。"""
        s = FailStreak()
        self.assertFalse(s.record(False))    # 第 1 轮
        self.assertFalse(s.record(False))    # 第 2 轮
        self.assertTrue(s.record(False))     # 第 3 轮 → 提醒
        self.assertFalse(s.record(False))    # 第 4 轮不再提醒
        self.assertFalse(s.record(False))    # 第 5 轮不再提醒

    def test_success_resets(self):
        """任一轮成功即清零，新一轮失败需重新累计 3 轮才提醒。"""
        s = FailStreak()
        for _ in range(2):
            s.record(False)
        self.assertFalse(s.record(True))     # 成功清零
        self.assertEqual(s.count, 0)
        self.assertFalse(s.record(False))
        self.assertFalse(s.record(False))
        self.assertTrue(s.record(False))     # 重新数到 3 才提醒

    def test_mixed_success_and_failure(self):
        s = FailStreak()
        self.assertFalse(s.record(False))    # 失败 1
        self.assertFalse(s.record(True))     # 成功清零
        self.assertFalse(s.record(False))    # 失败 1
        self.assertFalse(s.record(False))    # 失败 2
        self.assertTrue(s.record(False))     # 失败 3 → 提醒
        self.assertFalse(s.record(True))     # 成功清零

    def test_default_streak_is_three(self):
        self.assertEqual(FAIL_REMIND_STREAK, 3)
        self.assertEqual(FailStreak().remind_after, 3)


# ---------------- stale_suffix：上次成功时长文案 ----------------

class TestStaleSuffix(unittest.TestCase):

    NOW = datetime(2026, 11, 3, 12, 0, 0)

    def test_never_succeeded(self):
        self.assertEqual(stale_suffix(None, self.NOW), "")

    def test_within_one_minute_hidden(self):
        """刚成功（<60 秒）不展示，避免误导。"""
        self.assertEqual(stale_suffix(self.NOW - timedelta(seconds=30), self.NOW), "")
        self.assertEqual(stale_suffix(self.NOW, self.NOW), "")

    def test_minutes_floor_at_one(self):
        self.assertEqual(stale_suffix(self.NOW - timedelta(seconds=60), self.NOW),
                         "（上次成功刷新于 1 分钟前）")
        self.assertEqual(stale_suffix(self.NOW - timedelta(seconds=119), self.NOW),
                         "（上次成功刷新于 1 分钟前）")

    def test_minutes_value(self):
        self.assertEqual(stale_suffix(self.NOW - timedelta(minutes=5), self.NOW),
                         "（上次成功刷新于 5 分钟前）")
        self.assertEqual(stale_suffix(self.NOW - timedelta(hours=2), self.NOW),
                         "（上次成功刷新于 120 分钟前）")


# ---------------- http_client 请求头 ValueError 脱敏 ----------------

class _FakeOpener:
    def __init__(self, exc):
        self._exc = exc

    def open(self, req, timeout=None):
        raise self._exc


class TestHeaderValueSanitized(unittest.TestCase):

    def test_valueerror_fixed_message_no_leak(self):
        """urllib 对含换行的请求头抛 ValueError，原文携带头内容
        （即密钥）。返回值必须是固定文案，不含异常原文。"""
        secret = "SECRET-KEY-CONTENT"
        err = ValueError("Invalid header value %r" % (secret + "\n",))
        code, msg, ra = hc._http_json_once(
            _FakeOpener(err), "http://x", None, None, 5)
        self.assertEqual(code, 0)
        self.assertEqual(ra, 0.0)
        self.assertEqual(msg, "请求格式错误：请检查 API Key 是否误粘贴了换行或特殊字符")
        self.assertNotIn(secret, msg)

    def test_header_value_error_propagates_to_fixed_text(self):
        """带换行的头在真实请求路径上抛的 ValueError 同样收敛为固定文案。"""
        code, msg, _ra = hc._http_json_once(
            _FakeOpener(ValueError("Invalid header value b'bad\\nkey'")),
            "http://x", None, None, 5)
        self.assertEqual(code, 0)
        self.assertEqual(msg, "请求格式错误：请检查 API Key 是否误粘贴了换行或特殊字符")


if __name__ == "__main__":
    unittest.main()
