# -*- coding: utf-8 -*-
"""HTTP 基础：直连优先 → 系统代理回退 → 指数退避重试。

原 usage_widget.pyw 的 http_json 剥离而来；v3.2.0 起重试策略从
「全败歇 2 秒重试一轮」升级为指数退避，并尊重 429/503 的 Retry-After。"""

import json
import time
import urllib.error
import urllib.request

# 限流/服务暂时不可用：这类响应值得等待后重试（与网络层失败同待遇）
_RETRYABLE = (429, 503)
# 指数退避节奏：首次尝试后按 2/4/8 秒重试，最多 4 次尝试
_BACKOFFS = (2.0, 4.0, 8.0)
_RETRY_AFTER_CAP = 60.0   # Retry-After 封顶：个别服务给出一小时级等待
                          # 时不能把刷新线程卡死那么久


def _retry_after_of(e):
    """从 HTTPError 响应头读 Retry-After 秒数；缺失/非法收敛为 0，
    过长按 _RETRY_AFTER_CAP 封顶。"""
    try:
        v = float(e.headers.get("Retry-After", "0"))
    except Exception:
        return 0.0
    return max(0.0, min(v, _RETRY_AFTER_CAP))


def _http_json_once(opener, url, headers, data, timeout):
    """单路请求，返回 (code, parsed, retry_after)。

    code=0 表示网络层失败（连不上/超时/DNS）；retry_after 只在服务端
    返回 429/503 且带 Retry-After 头时有值，否则为 0。"""
    req = urllib.request.Request(url, headers=headers or {},
                                 data=data, method="POST" if data is not None else "GET")
    retry_after = 0.0
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
        if code in _RETRYABLE:
            retry_after = _retry_after_of(e)
    except ValueError as e:   # 请求头/参数格式问题（如密钥误粘贴带换行）
        return 0, "请求格式错误: %s" % e, 0.0
    except Exception as e:
        return 0, "网络错误: %s" % e, 0.0
    try:
        return code, json.loads(raw), retry_after
    except Exception:
        return code, raw, retry_after


def http_json(url, headers=None, data=None, timeout=20):
    """
    发起请求，返回 (status_code, dict|str)。
    策略：先直连（国内接口直连最稳，不受本地代理软件状态影响），
    直连在网络层失败时自动回退系统代理再试一次；两条路都在网络层
    失败（多为 DNS/路由间歇抖动），或服务端返回 429/503（限流/暂时
    不可用）时按指数退避重试：等待 2/4/8 秒，共 4 次尝试。服务端带
    Retry-After 头时优先尊重它（封顶 60 秒）。
    拿到其他任何 HTTP 响应（含 401/403）即返回，不做无谓重试；
    429/503 是服务端对密钥的判定，换代理路也躲不开，拿到即不再试。
    """
    def _round():
        result = (0, "未知错误", 0.0)
        for opener in (urllib.request.build_opener(urllib.request.ProxyHandler({})),
                       urllib.request.build_opener()):
            result = _http_json_once(opener, url, headers, data, timeout)
            if result[0] != 0:
                return result
        return result

    result = _round()
    for wait in _BACKOFFS:
        code = result[0]
        if code == 0:             # 网络层全败：错开抖动窗口
            pause = wait
        elif code in _RETRYABLE:  # 限流/暂时不可用：优先尊重服务端要求
            pause = max(result[2], wait)
        else:                     # 其他 HTTP 响应：立即返回
            return result[:2]
        time.sleep(pause)
        result = _round()
    return result[:2]
