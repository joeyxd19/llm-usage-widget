# -*- coding: utf-8 -*-
"""DeepSeek 开放平台账户余额查询 + 本地消耗推算。

官方接口（api-docs.deepseek.com/api/get-user-balance）：
GET https://api.deepseek.com/user/balance
Authorization: Bearer <API Key>
响应：{"is_available": bool,
       "balance_infos": [{"currency": "CNY"|"USD",
                          "total_balance": "110.00",      # 全部为字符串
                          "granted_balance": "10.00",
                          "topped_up_balance": "100.00"}]}

重要事实：DeepSeek 官方**没有**公开的用量/消耗查询接口（平台网页
「用量信息」仅支持手工导出 CSV）。消耗数据有两个来源：

1. 主路径：本地余额时间序列（history.jsonl 的 dsb/dsc 字段）推算，
   账本模型借鉴 MeteorNOX/DeepSeek-Balance-Whale-Widget 的
   accounting.mjs（余额下降累计为消耗，上涨单独记为入账，
   观测缺口超阈值标记数据不完整），见 balance_spent；
2. 实验路径（默认关闭）：调用未在官方文档公开的
   GET /v1/usage?start_date=&end_date=（Bearer 认证，日期为 UTC），
   社区实现（JayHome137/DeepSeekMonitor 等）实测部分账户返回 404。
   任何失败一律静默回退推算，UI 标注「官方·实验」。
"""

import datetime
import hashlib
import time
import urllib.parse

from .config import APP_VERSION, is_placeholder
from .http_client import http_json

BALANCE_URL = "https://api.deepseek.com/user/balance"
USAGE_URL = "https://api.deepseek.com/v1/usage"   # 未文档化，实验性

# 相邻余额观测缺口超过该值时，该段消耗无法观测，标记「数据不完整」
# （悬浮窗默认 5 分钟刷新，正常缺口远小于 10 分钟；程序关闭期间会超）
OBSERVATION_GAP_SECONDS = 10 * 60


def _to_money(v):
    """接口金额字符串 → float（两位小数）；非法返回 None。"""
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def key_fingerprint(api_key):
    """API Key 的短指纹（sha256 前 8 位十六进制），仅用于余额账本分段。

    历史快照里只存指纹、绝不存原 Key（指纹不可反推 Key）；
    换 Key / 换账户时指纹变化，balance_spent 据此丢弃跨账户差额。
    空 Key 返回空串（与旧历史记录缺失指纹的记账行为一致）。"""
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


def fetch_deepseek(dcfg):
    """
    查询账户余额（cfg dict 进、result dict 出，字段形状与其他 provider
    一致：ok/error/windows，另带余额与实验用量字段）。

    - balances：多币种逐条列出（不同币种分行展示、不做合计）；
      balance 恒等于 balances[0]，供历史快照与主展示行使用
    - usage：实验开关打开时才有官方用量，失败恒为 None（静默回退）
    """
    result = {"ok": False, "level": "", "windows": [], "balance": None,
              "balances": [], "usage": None, "error": ""}
    key = (dcfg.get("api_key") or "").strip()
    if is_placeholder(key):
        result["error"] = "未配置 API Key"
        return result
    code, body = http_json(BALANCE_URL, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "User-Agent": "usage-widget/" + APP_VERSION,
    })
    if code == 0:
        result["error"] = str(body)
        return result
    if code == 401:
        result["error"] = "API Key 无效或已过期"
        return result
    if code != 200:
        msg = ""
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or "")[:80]
        elif isinstance(body, str):
            msg = body[:80]
        result["error"] = "HTTP %s %s" % (code, msg or "查询失败")
        return result

    # ---- 畸形响应防御：结构非预期类型时收敛为统一中文错误，不抛异常 ----
    if not isinstance(body, dict):
        result["error"] = "响应结构异常"
        return result
    infos = body.get("balance_infos")
    if not isinstance(infos, list) or not infos \
            or any(not isinstance(i, dict) for i in infos):
        result["error"] = "响应结构异常"
        return result
    is_available = bool(body.get("is_available", False))
    for info in infos:
        total = _to_money(info.get("total_balance"))
        if total is None:   # 单条金额解析失败只丢弃该条，其余币种照常展示
            continue
        result["balances"].append({
            "is_available": is_available,
            "currency": str(info.get("currency") or "CNY"),
            "total": total,
            "granted": _to_money(info.get("granted_balance")),
            "topped_up": _to_money(info.get("topped_up_balance")),
        })
    if not result["balances"]:
        result["error"] = "响应结构异常"
        return result
    result["balance"] = result["balances"][0]

    if dcfg.get("experimental_usage", False):
        # 实验性官方用量（未文档化接口）：失败静默，不影响余额展示
        try:
            result["usage"] = fetch_usage_official(key)
        except Exception:
            result["usage"] = None

    result["ok"] = True
    return result


# --------------------------------------------------------------------------
# 实验性官方用量（未文档化接口，默认关闭）
# --------------------------------------------------------------------------

def _iso_day(d):
    return d.strftime("%Y-%m-%d")


USAGE_TIMEOUT = 10   # 实验接口未承诺 SLA：超时即放弃，静默回退余额推算


def _usage_query(key, start_day, end_day):
    """单次用量查询，返回 data 记录列表；任何非 200 / 畸形结构返回 None。

    超时显式收紧为 USAGE_TIMEOUT 秒（http_json 默认 20 秒且带指数
    退避重试，实验接口不值得等这么久）。"""
    qs = urllib.parse.urlencode({"start_date": _iso_day(start_day),
                                 "end_date": _iso_day(end_day)})
    code, body = http_json(USAGE_URL + "?" + qs, headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": "usage-widget/" + APP_VERSION,
    }, timeout=USAGE_TIMEOUT)
    if code != 200 or not isinstance(body, dict):
        return None
    data = body.get("data")
    return data if isinstance(data, list) else None


def _aggregate_usage(records):
    """聚合消耗记录（cost_in_cents 单位为分，币种实测为人民币）。
    畸形记录安全跳过；一条有效记录都没有视为结构异常，返回 None。
    返回 (总额, {模型: 金额}, {日期: 金额})——date 维度供
    fetch_usage_official 拆「今日/近 7 天」，避免二次请求。"""
    total = 0.0
    models = {}
    days = {}
    parsed = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        try:
            cost = float(rec.get("cost_in_cents") or 0) / 100.0
        except (TypeError, ValueError):
            continue
        parsed += 1
        model = str(rec.get("model_name") or "未知模型")
        day = str(rec.get("date") or "")
        total += cost
        models[model] = round(models.get(model, 0.0) + cost, 2)
        days[day] = round(days.get(day, 0.0) + cost, 2)
    if not parsed:
        return None
    return round(total, 2), models, days


def fetch_usage_official(key, now=None):
    """实验性：GET /v1/usage 一次查询「近 7 天」，本地按 date 拆出今日。

    接口本身支持日期区间，原「今日 + 近 7 天」两次串行请求浪费且
    翻倍触发 404/限流，现合并为一次请求：start=今天(UTC)-6 天、
    end=今天(UTC)，聚合后今日 = date 等于今日分组的合计，
    近 7 天 = 全部合计。

    未在官方文档公开，可能随时变更或对部分账户 404 —— 任何失败
    （网络、401、404、畸形结构）一律返回 None，调用方静默回退
    余额推算，绝不向用户弹错误、也不混进余额错误行。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    records = _usage_query(key, today - datetime.timedelta(days=6), today)
    if records is None:
        return None
    agg = _aggregate_usage(records)
    if agg is None:
        return None
    week_cost, week_models, by_day = agg
    today_cost = by_day.get(_iso_day(today))
    if today_cost is None:
        # 区间有效但今日无记录：今日消耗为 0（而非结构异常）
        today_cost = 0.0
        today_models = []
    else:
        # 今日模型明细按 date 分组重算：从原始记录再过一遍今日部分
        tm = {}
        for rec in records:
            if not isinstance(rec, dict) or str(rec.get("date") or "") != _iso_day(today):
                continue
            try:
                cost = float(rec.get("cost_in_cents") or 0) / 100.0
            except (TypeError, ValueError):
                continue
            m = str(rec.get("model_name") or "未知模型")
            tm[m] = round(tm.get(m, 0.0) + cost, 2)
        today_models = sorted(tm.items(), key=lambda kv: -kv[1])
    return {
        "today_cost": round(today_cost, 2),
        "today_models": today_models,
        "week_cost": week_cost,
        "week_models": sorted(week_models.items(), key=lambda kv: -kv[1]),
        "currency": "CNY",   # cost_in_cents 实测口径，未经官方确认
    }


# --------------------------------------------------------------------------
# 消耗推算（主路径：本地余额时间序列账本）
# --------------------------------------------------------------------------

def balance_spent(hist, hours, now=None):
    """推算近 hours 小时的消耗账本，返回 dict 或 None（样本不足保持安静）。

    返回：{"spent": 消耗, "credit": 入账(充值/赠送), "currency": 币种,
           "partial": 观测缺口导致数据不完整}
    账本模型（借鉴 whale-widget accounting.mjs）：
    - 相邻两次余额观测，余额下降累计为消耗、上涨单独记为「入账」，
      不冲减、不产生负消耗——充值不会被算成负数或拉低消耗；
    - 账本按 Key 指纹（dsf 字段）分段：跨指纹（换 Key/换账户）的余额
      差额既不算消耗也不算入账——那是两个账户的钱，不是消费；
      指纹缺失的旧记录视为同一未知账户（空指纹），同段内仍可推算；
    - 余额 < 0 的样本（欠费期）整点丢弃：欠费差额不是消耗，充值回正
      的差额也不是入账，欠费段整体不记账；
    - 相邻观测缺口或窗口起点缺口超过 10 分钟时 partial=True，
      表示该段消耗未被观测到（真实消耗只会更多）；
    - UI 必须标注「基于余额变化推算」。
    """
    if not isinstance(hist, list):
        return None
    now = time.time() if now is None else now
    cutoff = now - hours * 3600.0
    pts = []
    for rec in hist:
        if not isinstance(rec, dict):
            continue
        ts, v = rec.get("ts"), rec.get("dsb")
        if not isinstance(ts, (int, float)) or not isinstance(v, (int, float)):
            continue
        if ts < cutoff or ts > now or v < 0:
            continue
        pts.append((float(ts), float(v), str(rec.get("dsc") or ""),
                    str(rec.get("dsf") or "")))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    spent = 0.0
    credit = 0.0
    partial = (pts[0][0] - cutoff) > OBSERVATION_GAP_SECONDS
    for i in range(1, len(pts)):
        if pts[i][0] - pts[i - 1][0] > OBSERVATION_GAP_SECONDS:
            partial = True
        if pts[i][3] != pts[i - 1][3]:
            continue   # 跨账户（Key 指纹变化）：差额丢弃，不记账
        delta = pts[i - 1][1] - pts[i][1]
        if delta > 0:
            spent += delta
        elif delta < 0:
            credit += -delta
    return {"spent": round(spent, 2), "credit": round(credit, 2),
            "currency": pts[-1][2] or "CNY", "partial": partial}
