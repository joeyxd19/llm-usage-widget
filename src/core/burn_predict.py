# -*- coding: utf-8 -*-
"""本地用量历史 + 烧速预测（最小二乘外推）+ 时间格式化。

原 usage_widget.pyw 的历史读写、fmt_reset_info/fmt_duration 与
_window_points/_predict_calc/_predict_text 剥离而来。原先是 widget 方法、
依赖 self._hist / self.cfg 的三个函数改为把历史与配置作为参数传入，
算法与行为逐行不变。"""

import datetime
import json
import os
import time

from .config import HISTORY_NAME, HISTORY_KEEP_DAYS  # noqa: F401  (再导出，路径兼容)

# 预测适用的窗口（秒）：只对短周期窗口做烧速外推才有指导意义
PREDICT_WINDOWS = {"5小时": 5 * 3600, "本周": 7 * 86400}
# (供应商, 窗口名) → 历史快照字段名
HIST_KEY_MAP = {("zhipu", "5小时"): "z5", ("zhipu", "本周"): "zw",
                ("zhipu", "MCP"): "zm",
                ("volcano", "5小时"): "v5", ("volcano", "本周"): "vw",
                ("volcano", "本月"): "vm"}


# --------------------------------------------------------------------------
# 本地用量历史（烧速预测与趋势的数据基础）
# --------------------------------------------------------------------------

_history_last_prune = 0.0


def history_path():
    from .config import data_dir
    return os.path.join(data_dir(), HISTORY_NAME)


def append_history(record):
    """追加一条用量快照 {ts, z5, zw, zm, v5, vw, vm...}；
    顺带节流清理过期记录（每天最多一次），全部失败静默。"""
    global _history_last_prune
    try:
        with open(history_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return
    now = time.time()
    if now - _history_last_prune < 86400:
        return
    _history_last_prune = now
    prune_history(now - HISTORY_KEEP_DAYS * 86400)


def prune_history(cutoff_ts):
    """重写历史文件，只保留 cutoff_ts 之后的记录（原子替换）。"""
    try:
        kept = []
        with open(history_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = json.loads(line).get("ts")
                except Exception:
                    continue
                if isinstance(ts, (int, float)) and ts >= cutoff_ts:
                    kept.append(line)
        tmp = history_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")
        os.replace(tmp, history_path())
    except Exception:
        pass


def load_history(max_age_seconds):
    """读取最近 max_age_seconds 的快照，按时间升序返回 list[dict]。"""
    cutoff = time.time() - max_age_seconds
    out = []
    try:
        with open(history_path(), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts") if isinstance(rec, dict) else None
                if isinstance(ts, (int, float)) and ts >= cutoff:
                    out.append(rec)
        out.sort(key=lambda r: r.get("ts", 0))
    except Exception:
        return []
    return out


# --------------------------------------------------------------------------
# 时间格式化
# --------------------------------------------------------------------------

def norm_epoch(value):
    """兼容秒/毫秒时间戳，返回秒；无效返回 None。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 1e12:        # 毫秒级
        v /= 1000.0
    return v


def fmt_reset_info(value, now=None):
    """重置时间格式化：返回 (倒计时描述, 绝对时间描述)，无效时间返回 ("", "")。"""
    ts = norm_epoch(value)
    if ts is None:
        return "", ""
    if now is None:
        now = datetime.datetime.now()
    try:
        dt = datetime.datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return "", ""
    sec = (dt - now).total_seconds()
    if sec <= 0:
        cd = "即将重置"
    elif sec < 3600:
        cd = "%d 分钟后重置" % max(1, sec // 60)
    elif sec < 48 * 3600:
        cd = "%d 小时 %02d 分后重置" % (sec // 3600, (sec % 3600) // 60)
    elif sec < 8 * 86400:
        cd = "%d 天 %d 小时后重置" % (sec // 86400, (sec % 86400) // 3600)
    else:
        cd = ""
    if dt.date() == now.date():
        abs_s = "今天 " + dt.strftime("%H:%M")
    elif dt.date() == (now + datetime.timedelta(days=1)).date():
        abs_s = "明天 " + dt.strftime("%H:%M")
    else:
        wd = "一二三四五六日"[dt.weekday()]
        abs_s = "%d月%d日（周%s）%s" % (dt.month, dt.day, wd, dt.strftime("%H:%M"))
    return cd, abs_s


def fmt_duration(minutes):
    """分钟数 → 中文时长描述（预测行/通知文案用）。"""
    m = max(1, int(round(minutes)))
    if m < 60:
        return "%d 分钟" % m
    if m < 48 * 60:
        return "%d 小时 %02d 分" % (m // 60, m % 60)
    return "%d 天 %d 小时" % (m // 1440, (m % 1440) // 60)


# --------------------------------------------------------------------------
# 烧速预测（最小二乘外推）
# --------------------------------------------------------------------------

def window_points(hist, prov, nm, reset_ts):
    """当前窗口期内的历史点 [(ts, pct)]：优先按重置边界截断，
    边界未知时在百分比大幅回落（>25 点）处截断，识别新窗口。"""
    key = HIST_KEY_MAP.get((prov, nm))
    if not key:
        return []
    start = None
    dur = PREDICT_WINDOWS.get(nm)
    ts0 = norm_epoch(reset_ts)
    if dur and ts0:
        start = ts0 - dur
    pts = []
    for rec in hist:
        ts, v = rec.get("ts"), rec.get(key)
        if not isinstance(ts, (int, float)) or not isinstance(v, (int, float)):
            continue
        if start is not None and ts < start:
            continue
        pts.append((float(ts), float(v)))
    if not pts:
        return []
    cut = 0
    for i in range(1, len(pts)):
        if pts[i][1] < pts[i - 1][1] - 25:
            cut = i
    if cut:
        pts = pts[cut:]
    return pts


def predict_calc(hist, prov, nm, pct, reset_ts):
    """最小二乘烧速外推。数据不足（<4 点或跨度 <20 分钟）或烧速
    近乎零时返回 None，否则返回：
    slope(%/分) exhaust_min reset_min(可能 None) reset_pct(可能 None)。"""
    if nm not in PREDICT_WINDOWS or pct is None:
        return None
    pts = window_points(hist, prov, nm, reset_ts)
    now = time.time()
    pts = [p for p in pts if p[0] <= now]
    pts.append((now, float(pct)))
    if len(pts) < 4 or pts[-1][0] - pts[0][0] < 20 * 60:
        return None
    n = float(len(pts))
    mt = sum(p[0] for p in pts) / n
    mp = sum(p[1] for p in pts) / n
    s_tt = sum((p[0] - mt) ** 2 for p in pts)
    if s_tt <= 0:
        return None
    slope = sum((p[0] - mt) * (p[1] - mp) for p in pts) / s_tt * 60.0
    if slope < 0.02:          # %/分，近乎静止不预测
        return None
    reset_min = None
    ts0 = norm_epoch(reset_ts)
    if ts0:
        reset_min = (ts0 - now) / 60.0
        if reset_min <= 0:
            reset_min = None
    now_pct = pts[-1][1]
    return {"slope": slope,
            "exhaust_min": max(0.0, (100.0 - now_pct) / slope),
            "reset_min": reset_min,
            "reset_pct": (now_pct + slope * reset_min
                          if reset_min is not None else None)}


def predict_text(cfg, hist, prov, nm, pct, reset_ts):
    """详情面板预测行文案 (文本, 颜色键)；无预警价值时返回 None，
    避免面板常驻噪音。烧速预测默认关闭。"""
    if not cfg.get("burn_predict", False):
        return None
    calc = predict_calc(hist, prov, nm, pct, reset_ts)
    if not calc:
        return None
    slope_h = calc["slope"] * 60.0
    if calc["reset_min"] is not None and calc["exhaust_min"] < calc["reset_min"]:
        return ("烧速 %.1f%%/时 · 预计 %s后用尽（重置还需 %s）"
                % (slope_h, fmt_duration(calc["exhaust_min"]),
                   fmt_duration(calc["reset_min"])), "crit")
    if (calc["reset_min"] is None and calc["exhaust_min"] < 240):
        return ("烧速 %.1f%%/时 · 预计 %s后用尽"
                % (slope_h, fmt_duration(calc["exhaust_min"])), "warn")
    warn_at = float(cfg.get("warn_percent", 80))
    if calc["reset_pct"] is not None and calc["reset_pct"] >= warn_at:
        return ("烧速 %.1f%%/时 · 按此速度到重置约 %d%%"
                % (slope_h, min(100, round(calc["reset_pct"]))), "warn")
    return None
