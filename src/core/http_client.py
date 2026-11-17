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


def _openers():
    """两路 opener：直连优先（国内接口最稳），其次系统代理。"""
    return (urllib.request.build_opener(urllib.request.ProxyHandler({})),
            urllib.request.build_opener())


def open_url(url, headers=None, timeout=30):
    """打开 URL 返回响应对象（供大文件流式下载）。

    直连在网络层失败时回退系统代理；HTTPError（404/403 等服务端响应）
    直接抛出不换路——服务端判定与走哪条网络路径无关。两条路都在网络
    层失败时抛出最后一个异常。"""
    last = None
    for opener in _openers():
        req = urllib.request.Request(url, headers=headers or {})
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last = e
    raise last


def _brief_error(e, limit=200):
    """异常的一句话摘要（类型 + 截断信息），压掉换行。

    只用于网络层异常：连接被拒/DNS/超时/证书这类文案不含密钥。"""
    try:
        msg = " ".join(str(e).split())
    except Exception:
        msg = ""
    if len(msg) > limit:
        msg = msg[:limit] + "…"
    return "%s: %s" % (type(e).__name__, msg) if msg else type(e).__name__


def classify_error(detail):
    """把失败摘要归类成一句用户能看懂的原因（UI 与 error.log 共用）。"""
    text = str(detail or "")
    low = text.lower()
    if "unicodeencodeerror" in low or "请求头编码失败" in text:
        return "请求头编码失败（含非 ASCII 字符）"
    if "请求格式错误" in text:
        return "请求参数格式错误"
    if ("certificate verify" in low or "sslcertverification" in low
            or "certificate_verify_failed" in low):
        return "证书校验失败"
    if ("connection refused" in low or "10061" in low
            or "errno 111" in low):
        return "代理连接被拒绝"
    if ("gaierror" in low or "getaddrinfo" in low or "11001" in low
            or "name or service not known" in low
            or "nodename nor servname" in low):
        return "DNS 解析失败"
    if "timed out" in low or "timeout" in low or "10060" in low:
        return "连接超时"
    if "10054" in low or "connection reset" in low or "connection aborted" in low:
        return "连接被重置"
    if "ssl" in low:
        return "TLS 握手失败"
    return "网络错误"


def _is_local_format_error(detail):
    """本地确定性错误（请求头/参数格式），换网络路径重试无意义。"""
    text = str(detail or "")
    return "UnicodeEncodeError" in text or "请求格式错误" in text


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
    except UnicodeEncodeError as e:
        # http.client 按 latin-1 编码请求头，中文等非 ASCII 字符会在这里失败
        # （例如 User-Agent 用了中文名）。str(e) 只含编码名与位置，不含原文。
        return 0, "请求头编码失败（UnicodeEncodeError）: %s" % e, 0.0
    except ValueError:   # 请求头/参数格式问题（如密钥误粘贴带换行）
        # 固定文案，不拼接异常原文：ValueError 文案会携带非法请求头内容，
        # 即密钥原文（如 Invalid header value b'...'），拼进错误串会泄露
        return 0, "请求格式错误：请检查 API Key 是否误粘贴了换行或特殊字符", 0.0
    except Exception as e:
        return 0, "网络错误: %s" % _brief_error(e), 0.0
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
        for opener in _openers():
            result = _http_json_once(opener, url, headers, data, timeout)
            if result[0] != 0:
                return result
        return result

    result = _round()
    if result[0] == 0 and _is_local_format_error(result[1]):
        # 本地请求格式错误（如中文请求头）：两条路都会同样失败，退避重试
        # 只会白等 14 秒，直接返回让 UI 尽快给出具体原因。
        return result[:2]
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
