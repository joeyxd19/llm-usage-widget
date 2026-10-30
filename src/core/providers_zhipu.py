# -*- coding: utf-8 -*-
"""智谱 GLM Coding Plan 用量查询。

原 usage_widget.pyw 的 fetch_zhipu / fetch_zhipu_30d 原样剥离：
- 5 小时 / 本周窗口：靠 unit/number 显式区分（不能按重置时间排序猜）
- MCP 每月额度（TIME_LIMIT）：用量、总额、分模型明细
- 近 30 / 15 / 7 天 token 用量：独立接口，失败静默降级
"""

import datetime
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .config import APP_VERSION, is_placeholder
from .http_client import http_json

ZHIPU_ERROR_MAP = {
    401: "API Key 无效或已过期",
    1001: "缺少认证信息",
}


def fetch_zhipu(zcfg):
    """
    GET https://open.bigmodel.cn/api/monitor/usage/quota/limit
    认证: Authorization: <API_KEY>（不带 Bearer）
    """
    result = {"ok": False, "level": "", "five_hour": None,
              "weekly": None, "mcp": None, "windows": [],
              "thirty_day": None, "fifteen_day": None,
              "seven_day": None, "error": ""}
    key = (zcfg.get("api_key") or "").strip()
    if is_placeholder(key):
        result["error"] = "未配置 API Key"
        return result
    base = (zcfg.get("base_url") or "open.bigmodel.cn").strip().rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    url = base + "/api/monitor/usage/quota/limit"
    code, body = http_json(url, headers={
        "Authorization": key,
        "Content-Type": "application/json",
        "User-Agent": "usage-widget/" + APP_VERSION,
    })
    if code == 0:
        result["error"] = str(body)
        return result
    if not isinstance(body, dict) or not body.get("success"):
        msg = body.get("msg") if isinstance(body, dict) else str(body)[:80]
        if isinstance(body, dict):
            msg = ZHIPU_ERROR_MAP.get(body.get("code")) or msg
        result["error"] = "HTTP %s %s" % (code, msg or "查询失败")
        return result

    data = body.get("data") or {}
    result["level"] = str(data.get("level") or "").upper()
    limits = data.get("limits") or []

    tokens = [l for l in limits if l.get("type") == "TOKENS_LIMIT"]
    # 两个 TOKENS_LIMIT 靠 unit/number 显式区分（接口实测，v2.3.6）：
    #   5 小时窗口 unit=3 number=5；本周窗口 unit=6 number=1。
    # 不能按 nextResetTime 排序猜：5 小时窗口未激活时该字段可能缺失，
    # 且周窗口的重置点也可能早于下一个 5 小时边界，排序必然出错。
    def _is_five_hour(l):
        try:
            return int(l.get("number") or 0) == 5 and int(l.get("unit") or 0) == 3
        except Exception:
            return False

    def _reset_key(l):
        v = l.get("nextResetTime")
        return v if isinstance(v, (int, float)) else float("inf")

    five = next((l for l in tokens if _is_five_hour(l)), None)
    week = next((l for l in tokens if not _is_five_hour(l)), None) \
        if len(tokens) >= 2 else None
    if five is None and week is None and tokens:
        # unit/number 全缺失时兜底：重置早的当 5 小时窗口
        rest = sorted(tokens, key=_reset_key)
        five = rest[0]
        week = rest[1] if len(rest) >= 2 else None
    if five is not None:
        result["five_hour"] = five.get("percentage")
    if week is not None:
        result["weekly"] = week.get("percentage")
    # 完整窗口明细（详情面板用）：(名称, 百分比, 重置时间戳)，固定 5 小时在前
    for name, l in (("5小时", five), ("本周", week)):
        if l is not None:
            result["windows"].append(
                (name, l.get("percentage"), l.get("nextResetTime")))

    mcp = next((l for l in limits if l.get("type") == "TIME_LIMIT"), None)
    if mcp:
        total = mcp.get("usage") or 0
        used = mcp.get("currentValue") or 0
        pct = mcp.get("percentage")
        if pct is None and total:
            pct = round(used * 100.0 / total, 1)
        details = [(str(d.get("modelCode") or ""), d.get("usage") or 0)
                   for d in (mcp.get("usageDetails") or [])
                   if isinstance(d, dict)]
        result["mcp"] = {"used": used, "total": total, "percent": pct,
                         "reset_ms": mcp.get("nextResetTime"),
                         "details": details}

    # 近 N 天 token 用量：独立接口，失败静默降级，不影响主数据展示。
    # 三个区间并行请求，避免串行叠加等待
    day_jobs = [(k, d) for k, d, flag in
                (("thirty_day", 30, "show_30d"),
                 ("fifteen_day", 15, "show_15d"),
                 ("seven_day", 7, "show_7d"))
                if zcfg.get(flag, True)]
    if day_jobs:
        with ThreadPoolExecutor(max_workers=len(day_jobs)) as pool:
            futs = {k: pool.submit(fetch_zhipu_30d, base, key, days=d)
                    for k, d in day_jobs}
            for k, fut in futs.items():
                try:
                    result[k] = fut.result()
                except Exception:
                    result[k] = None

    result["ok"] = True
    return result


def fetch_zhipu_30d(base, key, days=30):
    """
    GET {base}/api/monitor/usage/model-usage?startTime=..&endTime=..
    与 quota/limit 同族同认证（Authorization 直接放 API Key，不带 Bearer）。
    返回 data.totalUsage.totalTokensUsage / totalModelCallCount，
    即 [startTime, endTime] 区间内的 token 总量与调用总次数。

    接口无公开文档（网页控制台同款），任何失败一律返回 None，
    由调用方静默降级隐藏「近 30 天」行，不影响原有额度显示。
    时间格式实测为 "YYYY-MM-DD HH:mm:ss"（本地时间）。
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=days)
    qs = urllib.parse.urlencode({"startTime": start.strftime(fmt),
                                 "endTime": end.strftime(fmt)})
    code, body = http_json(base + "/api/monitor/usage/model-usage?" + qs, headers={
        "Authorization": key,
        "Content-Type": "application/json",
        "User-Agent": "usage-widget/" + APP_VERSION,
    })
    if code == 0 or not isinstance(body, dict) or not body.get("success"):
        return None
    total = (body.get("data") or {}).get("totalUsage") or {}
    tokens = total.get("totalTokensUsage")
    if tokens is None:
        return None
    try:
        return {"tokens": int(tokens),
                "calls": int(total.get("totalModelCallCount") or 0)}
    except (TypeError, ValueError):
        return None
