# -*- coding: utf-8 -*-
"""火山引擎 Agent Plan 用量查询（Volcano Signature V4，AWS SigV4 变体）。

原 usage_widget.pyw 的 _hmac_sha256 / fetch_volcano 原样剥离。"""

import datetime
import hashlib
import hmac

from .config import APP_VERSION, is_placeholder
from .http_client import http_json


def _hmac_sha256(key, msg):
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def fetch_volcano(vcfg):
    """
    POST https://ark.<region>.volcengineapi.com/?Action=GetAFPUsage&Version=2024-01-01
    """
    result = {"ok": False, "level": "", "windows": [], "error": ""}
    ak = (vcfg.get("access_key_id") or "").strip()
    sk = (vcfg.get("secret_access_key") or "").strip()
    # 兼容误粘贴：整段 "AK\nSecret Access Key:xxx" 粘进了 AK 栏，自动拆分
    if "Secret Access Key" in ak:
        head, _, tail = ak.partition("\n")
        if not sk:
            sk = tail.split(":", 1)[-1].strip()
        ak = head.strip()
    if is_placeholder(ak) or is_placeholder(sk):
        result["error"] = "未配置 AK/SK"
        return result
    region = vcfg.get("region") or "cn-beijing"
    host = "ark.%s.volcengineapi.com" % region
    service = "ark"

    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    body = "{}"
    payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    content_type = "application/json; charset=utf-8"

    query = "Action=GetAFPUsage&Version=2024-01-01"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:%s\nhost:%s\nx-content-sha256:%s\nx-date:%s\n"
        % (content_type, host, payload_hash, x_date)
    )
    canonical_request = "\n".join([
        "POST", "/", query, canonical_headers, signed_headers, payload_hash,
    ])

    scope = "%s/%s/%s/request" % (short_date, region, service)
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k_date = _hmac_sha256(sk.encode("utf-8"), short_date)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    authorization = ("HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                     % (ak, scope, signed_headers, signature))

    url = "https://%s/?%s" % (host, query)
    code, body = http_json(url, headers={
        "Content-Type": content_type,
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
        "User-Agent": "usage-widget/" + APP_VERSION,
    }, data=body.encode("utf-8"))

    if code == 0:
        result["error"] = str(body)
        return result
    if not isinstance(body, dict):
        result["error"] = "HTTP %s %s" % (code, str(body)[:80])
        return result
    meta = body.get("ResponseMetadata") or {}
    if meta.get("Error"):
        err = meta["Error"]
        code_s = str(err.get("Code") or "Error")
        hints = {
            "AccessDenied": "AK/SK 没有方舟查询权限：请在火山引擎控制台为该子用户添加 ArkFullAccess（或 ark:GetAFPUsage 只读）授权",
            "SignatureDoesNotMatch": "Secret Access Key 不正确",
            "InvalidCredential": "AK/SK 无效或已被禁用",
        }
        if code_s in hints:
            result["error"] = hints[code_s]
        else:
            result["error"] = "%s: %s" % (code_s, (err.get("Message") or "")[:80])
        return result

    res = body.get("Result") or {}
    result["level"] = str(res.get("PlanType") or "")
    # 官方套餐只有 5 小时 / 周 / 月 三档限额；API 返回的 AFPDaily（今日，
    # 固定 10000）比周限额还大，永远不会成为有效约束，不展示
    for key, name in (("AFPFiveHour", "5小时"),
                      ("AFPWeekly", "本周"), ("AFPMonthly", "本月")):
        w = res.get(key)
        if isinstance(w, dict) and w.get("Quota"):
            result["windows"].append((name, w.get("Used", 0), w["Quota"],
                                      w.get("ResetTime") or 0))
    result["ok"] = True
    return result
