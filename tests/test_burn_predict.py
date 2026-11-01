# -*- coding: utf-8 -*-
"""core/burn_predict.py 纯逻辑单测：时间格式化、窗口截断与烧速外推。

历史点用 time.time() 现场构造，全部断言不依赖固定日期。"""

import datetime
import time

import pytest

from core import burn_predict as bp

NOW = time.time()


def _rec(offset_min, z5):
    """一条 z5（智谱 5 小时窗口）历史快照，offset_min 为距现在的分钟数。"""
    return {"ts": NOW - offset_min * 60, "z5": z5}


# ---------------- 时间戳归一 / 格式化 ----------------

def test_norm_epoch():
    assert bp.norm_epoch(1_700_000_000) == 1_700_000_000.0
    assert bp.norm_epoch(1_700_000_000_000) == 1_700_000_000.0   # 毫秒级
    assert bp.norm_epoch("1700000000") == 1_700_000_000.0
    assert bp.norm_epoch(0) is None
    assert bp.norm_epoch(-5) is None
    assert bp.norm_epoch("abc") is None
    assert bp.norm_epoch(None) is None


def test_fmt_duration():
    assert bp.fmt_duration(5) == "5 分钟"
    assert bp.fmt_duration(90) == "1 小时 30 分"
    assert bp.fmt_duration(3000) == "2 天 2 小时"
    assert bp.fmt_duration(-3) == "1 分钟"   # 负值收敛到 1 分钟


def test_fmt_reset_info():
    now = datetime.datetime(2026, 3, 15, 12, 0, 0)
    # 30 分钟后重置
    cd, abs_s = bp.fmt_reset_info(
        (now + datetime.timedelta(minutes=30)).timestamp(), now=now)
    assert cd == "30 分钟后重置"
    assert abs_s == "今天 12:30"
    # 20 小时后 → 明天
    cd, abs_s = bp.fmt_reset_info(
        (now + datetime.timedelta(hours=20)).timestamp(), now=now)
    assert cd == "20 小时 00 分后重置"
    assert abs_s == "明天 08:00"
    # 已过期 → 即将重置
    cd, _ = bp.fmt_reset_info(
        (now - datetime.timedelta(minutes=5)).timestamp(), now=now)
    assert cd == "即将重置"
    # 无效时间
    assert bp.fmt_reset_info(None) == ("", "")
    assert bp.fmt_reset_info("abc") == ("", "")


# ---------------- 窗口截断 ----------------

def test_window_points_reset_boundary():
    # 重置点在 4 小时后 → 5 小时窗口始于 1 小时前，90 分钟前的点被截掉
    reset = NOW + 4 * 3600
    hist = [_rec(90, 50), _rec(30, 60)]
    pts = bp.window_points(hist, "zhipu", "5小时", reset)
    assert len(pts) == 1
    assert pts[0][1] == 60


def test_window_points_drop_cutoff():
    # 80 → 30 回落 50 点（>25）：识别为新窗口起点，只保留回落之后的点
    hist = [_rec(40, 80), _rec(20, 30), _rec(10, 35)]
    pts = bp.window_points(hist, "zhipu", "5小时", None)
    assert len(pts) == 2
    assert pts[0][1] == 30


def test_window_points_unknown_name():
    assert bp.window_points([], "zhipu", "不存在的窗口", None) == []


# ---------------- 烧速外推 ----------------

def _rising_hist():
    """60 分钟内从 10% 匀速涨到 70% 的 4 个历史点。"""
    return [_rec(60, 10), _rec(45, 30), _rec(30, 50), _rec(15, 70)]


def test_predict_calc_ok():
    r = bp.predict_calc(_rising_hist(), "zhipu", "5小时", 70, None)
    assert r is not None
    # 最小二乘拟合斜率：64 %/小时 = 1.0667 %/分
    assert r["slope"] == pytest.approx(64.0 / 60.0, rel=0.02)
    assert r["reset_min"] is None
    assert 0 <= r["exhaust_min"] < 240


def test_predict_calc_with_reset():
    reset = NOW + 75 * 60   # 重置在 75 分钟后（窗口始于 3h45m 前，不截点）
    r = bp.predict_calc(_rising_hist(), "zhipu", "5小时", 70, reset)
    assert r is not None
    assert r["reset_min"] == pytest.approx(75, abs=1.0)
    assert r["exhaust_min"] < r["reset_min"]   # 重置前就会用尽
    assert r["reset_pct"] is not None


def test_predict_calc_insufficient_data():
    hist = [_rec(30, 10), _rec(10, 50)]   # 2 点 + 当前 1 点 < 4
    assert bp.predict_calc(hist, "zhipu", "5小时", 50, None) is None
    assert bp.predict_calc([], "zhipu", "5小时", 50, None) is None


def test_predict_calc_flat_slope():
    hist = [_rec(60, 50), _rec(40, 50), _rec(20, 50), _rec(10, 50)]
    assert bp.predict_calc(hist, "zhipu", "5小时", 50, None) is None


def test_predict_calc_not_predictable_window():
    assert bp.predict_calc([], "zhipu", "MCP", 50, None) is None
    assert bp.predict_calc([], "zhipu", "5小时", None, None) is None


# ---------------- 预测文案（开关门控 + 颜色分级） ----------------

def test_predict_text_gated_off():
    assert bp.predict_text({"burn_predict": False}, _rising_hist(),
                           "zhipu", "5小时", 70, None) is None


def test_predict_text_warn_no_reset():
    cfg = {"burn_predict": True, "warn_percent": 80}
    out = bp.predict_text(cfg, _rising_hist(), "zhipu", "5小时", 70, None)
    assert out is not None
    text, color = out
    assert "用尽" in text
    assert color == "warn"


def test_predict_text_crit_exhaust_before_reset():
    cfg = {"burn_predict": True, "warn_percent": 80}
    reset = NOW + 75 * 60
    out = bp.predict_text(cfg, _rising_hist(), "zhipu", "5小时", 70, reset)
    assert out is not None
    text, color = out
    assert "重置还需" in text
    assert color == "crit"
