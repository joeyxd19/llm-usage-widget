# -*- coding: utf-8 -*-
"""HTTP 基础：直连优先 → 系统代理回退 → 网络层全败重试一轮。

原 usage_widget.pyw 的 http_json 原样剥离，行为不变。"""

import json
import time
import urllib.error
import urllib.request


def _http_json_once(opener, url, headers, data, timeout):
    req = urllib.request.Request(url, headers=headers or {},
                                 data=data, method="POST" if data is not None else "GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except ValueError as e:   # 请求头/参数格式问题（如密钥误粘贴带换行）
        return 0, "请求格式错误: %s" % e
    except Exception as e:
        return 0, "网络错误: %s" % e
    try:
        return code, json.loads(raw)
    except Exception:
        return code, raw


def http_json(url, headers=None, data=None, timeout=20):
    """
    发起请求，返回 (status_code, dict|str)。
    策略：先直连（国内接口直连最稳，不受本地代理软件状态影响），
    直连在网络层失败时自动回退系统代理再试一次；两条路都在网络层
    失败（多为 DNS/路由间歇抖动）时，停 2 秒再完整重试一轮。
    拿到任何 HTTP 响应（含 401/503）即返回，不做无谓重试。
    """
    def _round():
        result = (0, "未知错误")
        for opener in (urllib.request.build_opener(urllib.request.ProxyHandler({})),
                       urllib.request.build_opener()):
            result = _http_json_once(opener, url, headers, data, timeout)
            if result[0] != 0:
                return result
        return result
    result = _round()
    if result[0] == 0:      # 网络层全败：歇 2 秒，把 DNS 抖动窗口错过去
        time.sleep(2)
        result = _round()
    return result
