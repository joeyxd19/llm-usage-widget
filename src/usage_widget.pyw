# -*- coding: utf-8 -*-
"""
额度悬浮窗 v2.3.2 - 智谱 GLM Coding Plan + 火山引擎 Agent Plan 桌面常驻用量监控

v2.3.2 更新：
- 修复火山引擎"网络错误"：误把整段 "AK + Secret Access Key:xxx" 粘进
  AK 栏时，请求头带换行导致请求发不出去；现在自动拆分出 AK/Secret
- 密钥读取统一 strip 清洗；请求头格式错误与真网络错误分开提示
- 网络加固：本机代理客户端虚拟网卡会导致 DNS/路由间歇抖动（实测偶发
  getaddrinfo 失败、单连接卡 15~35 秒）；超时 15s → 20s，直连+系统代理
  两条路全部在网络层失败时停 2 秒自动完整重试一轮

v2.3.1 更新：
- 贴边把手缩短：120k → 70k（200% 缩放下约 140 像素的短条）

v2.3 更新：
- 移除卡片式/圆环式两种形态与右键「显示形态」菜单：主窗口直接显示完整详情面板，
  贴边悬停展开的也是同一个面板，逻辑更简单直接
- 验证并确认 5 小时/本周窗口映射正确（5 小时窗口每 5 小时整清零一次）
- 版本管理：源码目录 git 仓库 + 版本备份文件夹（每版含 EXE，可整体回退）

v2.2 更新：
- 详情面板：各窗口额度百分比、重置倒计时与绝对时间、MCP 分模型用量、
  套餐等级、更新时间；数据超过 2 分钟未刷新时展开自动拉取；
  面板停留期间每 30 秒刷新倒计时

v2.1 更新：
- 贴边自动收起：拖到屏幕边缘松手即滑入收起，边缘只留一个进度小条
  （两条微型进度条持续显示用量），鼠标移上去自动展开详情面板
- 配色主题：深色 / 浅色 / 自动（感应窗口背后的背景明暗自动切换）

v2.0 更新：
- 修复高分屏/系统缩放下文字模糊发虚的问题（DPI 感知渲染）
- 新增图形化设置窗口：密钥、界面缩放、透明度、刷新间隔、阈值、供应商开关
- 修复严重 bug：运行期间手动编辑 config.json 填写的密钥会被旧配置覆盖丢失
  （现在所有保存均为"读盘-合并-写回"的增量保存）
- 界面缩放可调（紧凑 75% ~ 大 130%），可只显示其中一家供应商
- 双击 EXE / .pyw 即可运行，单实例保护，窗口位置超出屏幕自动复位
- 崩溃自动写入同目录 error.log 便于排查

零第三方依赖，仅需 Python 3.8+（Windows 官方安装包自带 tkinter）。
"""

import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request

try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox
    import tkinter.ttk as ttk
    TK_AVAILABLE = True
except ImportError:  # 允许无图形环境下导入 API 层
    TK_AVAILABLE = False
    tk = None
    tkfont = None
    ttk = None
    messagebox = None

APP_NAME = "额度悬浮窗"
APP_VERSION = "2.4.1"
CONFIG_NAME = "config.json"

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "zhipu": {
        "enabled": True,
        "api_key": "在这里填你的智谱API_Key",
        "base_url": "open.bigmodel.cn",
    },
    "volcano": {
        "enabled": True,
        "access_key_id": "在这里填火山AccessKeyID_AKLT开头",
        "secret_access_key": "在这里填火山SecretAccessKey",
        "region": "cn-beijing",
    },
    "refresh_minutes": 5,
    "opacity": 0.96,
    "ui_scale": 1.0,
    "warn_percent": 80,
    "critical_percent": 95,
    "window_x": 120,
    "window_y": 120,
    "theme": "auto",           # auto 自动跟随背景 | dark 深色 | light 浅色
    "edge_dock": True,         # 贴边自动收起
    "dock_side": "none",       # none/left/right/top/bottom（上次收起的边）
}

SCALE_STEPS = [0.75, 0.9, 1.0, 1.15, 1.3]
SCALE_LABELS = {
    0.75: "75%（紧凑）",
    0.9: "90%",
    1.0: "100%（标准）",
    1.15: "115%",
    1.3: "130%（大）",
}


def app_dir():
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(app_dir(), CONFIG_NAME)


def _deep_merge(dst, src):
    """递归合并 src 到 dst（dict 深合并，其余直接覆盖）。"""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def _load_file_config():
    """只读磁盘文件里的原始配置（不合并默认值），失败返回空 dict。"""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config():
    """默认配置 + 磁盘用户配置，返回运行用完整配置。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    user = _load_file_config()
    for section in ("zhipu", "volcano"):
        if isinstance(user.get(section), dict):
            cfg[section].update(user[section])
    for key in ("refresh_minutes", "opacity", "ui_scale", "warn_percent",
                "critical_percent", "window_x", "window_y",
                "theme", "edge_dock", "dock_side"):
        if key in user:
            cfg[key] = user[key]
    return cfg


def save_config_patch(patch):
    """
    增量保存：先读磁盘最新内容（用户可能刚手动编辑过），合并 patch 后写回。
    从根本上避免"运行中的旧内存配置覆盖用户刚填的密钥"。
    """
    base = _load_file_config()
    if not base:
        base = json.loads(json.dumps(DEFAULT_CONFIG))
    _deep_merge(base, patch)
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def is_placeholder(value):
    return (not value) or ("这里填" in str(value)) or str(value).strip() == ""


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


# --------------------------------------------------------------------------
# DPI / 系统环境
# --------------------------------------------------------------------------

def enable_high_dpi():
    """让进程声明 DPI 感知，否则 Windows 会对窗口做位图拉伸导致文字发虚。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            # Per-Monitor v2 (Win10 1703+)
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
            return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


def get_screen_dpi():
    """获取主屏实际 DPI（DPI 感知开启后读取的才是真实值）。"""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            dc = user32.GetDC(0)
            try:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
            finally:
                user32.ReleaseDC(0, dc)
            if dpi and dpi > 0:
                return int(dpi)
        except Exception:
            pass
    return 96


def sample_background_luminance(x, y, w, h, screen_w, screen_h):
    """采样矩形四周（外扩 8px）背景亮度的中位数（0~255）。
    只取窗口外的点，不会被悬浮窗自己污染；失败返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        dc = user32.GetDC(0)
        try:
            off = 8
            pts = [
                (x - off, y - off), (x + w + off, y - off),
                (x - off, y + h + off), (x + w + off, y + h + off),
                (x + w // 2, y - off), (x + w // 2, y + h + off),
                (x - off, y + h // 2), (x + w + off, y + h // 2),
            ]
            lums = []
            for px, py in pts:
                if px < 0 or py < 0 or px >= screen_w or py >= screen_h:
                    continue
                c = gdi32.GetPixel(dc, int(px), int(py))  # COLORREF 0x00BBGGRR
                if c < 0:                                 # CLR_INVALID
                    continue
                r = c & 0xFF
                g = (c >> 8) & 0xFF
                b = (c >> 16) & 0xFF
                lums.append(0.299 * r + 0.587 * g + 0.114 * b)
            if not lums:
                return None
            lums.sort()
            return lums[len(lums) // 2]
        finally:
            user32.ReleaseDC(0, dc)
    except Exception:
        return None


_MUTEX_HANDLE = None


def acquire_single_instance_mutex():
    """单实例保护；返回 None 表示已有实例在运行。"""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        _MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(
            None, False, "UsageWidget_ZV_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return True
    except Exception:
        return True


def _excepthook(exc_type, exc, tb):
    """无控制台模式下把崩溃写入 error.log，便于排查。"""
    try:
        with open(os.path.join(app_dir(), "error.log"), "a", encoding="utf-8") as f:
            f.write("\n[%s]\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass


sys.excepthook = _excepthook


# --------------------------------------------------------------------------
# HTTP 基础
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# 智谱 GLM Coding Plan
# --------------------------------------------------------------------------

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
              "weekly": None, "mcp": None, "windows": [], "error": ""}
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

    result["ok"] = True
    return result


# --------------------------------------------------------------------------
# 火山引擎 Agent Plan（Volcano Signature V4，AWS SigV4 变体）
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# UI 主题
# --------------------------------------------------------------------------

C_BG        = "#17181d"   # 窗体背景（同时也是透明色）
C_CARD      = "#232429"   # 卡片
C_CARD_LINE = "#2e3038"
C_TEXT      = "#e8eaed"
C_TEXT_DIM  = "#9aa0a6"
C_TRACK     = "#3a3d46"   # 进度条底槽
C_GREEN     = "#4ade80"
C_YELLOW    = "#fbbf24"
C_RED       = "#f87171"
C_ACCENT_Z  = "#7aa2ff"   # 智谱品牌蓝
C_ACCENT_V  = "#ff7a90"   # 火山品牌红粉

# 深浅两套主题；C_BG 透明色两套共用（chroma key 与主题无关）
THEMES = {
    "dark": {
        "card": C_CARD, "line": C_CARD_LINE, "text": C_TEXT,
        "dim": C_TEXT_DIM, "track": C_TRACK,
        "ok": C_GREEN, "warn": C_YELLOW, "crit": C_RED,
        "tag_z": "#2a3550", "tag_v": "#45242e",
    },
    "light": {
        "card": "#ffffff", "line": "#d8dce2", "text": "#202124",
        "dim": "#5f6368", "track": "#e6e8ec",
        "ok": "#16a34a", "warn": "#d97706", "crit": "#dc2626",
        "tag_z": "#e8eeff", "tag_v": "#ffe9ee",
    },
}
THEME_LABELS = {"auto": "自动（跟随背景）", "dark": "深色", "light": "浅色"}

FONT_FAMILY_CANDIDATES = ["Microsoft YaHei UI", "Microsoft YaHei",
                          "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei"]


# --------------------------------------------------------------------------
# 设置窗口
# --------------------------------------------------------------------------

class SettingsDialog(tk.Toplevel):
    """图形化设置：供应商密钥 + 显示效果，保存走增量合并。"""

    def __init__(self, master, cfg, on_apply, on_autostart):
        super().__init__(master)
        self.title("设置 - " + APP_NAME)
        self.configure(bg="#f0f0f0")
        self.resizable(False, False)
        self.transient(master)
        self.on_apply = on_apply
        self.on_autostart = on_autostart  # (enable_cb, disable_cb)

        style = ttk.Style(self)
        for theme in ("vista", "winnative", "clam"):
            try:
                style.theme_use(theme)
                break
            except Exception:
                continue

        zcfg = cfg.get("zhipu", {})
        vcfg = cfg.get("volcano", {})

        nb = ttk.Notebook(self)
        page_p = ttk.Frame(nb, padding=16)
        page_d = ttk.Frame(nb, padding=16)
        nb.add(page_p, text="  供应商 ")
        nb.add(page_d, text="  显示 ")
        nb.pack(fill="both", expand=True, padx=12, pady=(12, 4))

        # ================= 供应商页 =================
        self.z_en = tk.BooleanVar(value=bool(zcfg.get("enabled", True)))
        ttk.Checkbutton(page_p, text="启用 智谱 Coding Plan",
                        variable=self.z_en).pack(anchor="w", pady=(0, 6))
        self.z_key = tk.StringVar(value=zcfg.get("api_key", ""))
        self._secret_row(page_p, "API Key", self.z_key,
                         hint="智谱开放平台 → API Keys 页面")
        self.z_base = tk.StringVar(value=zcfg.get("base_url", "open.bigmodel.cn"))
        self._entry_row(page_p, "接口地址", self.z_base,
                        hint="国际版填 api.z.ai，一般不用改")

        ttk.Separator(page_p).pack(fill="x", pady=12)

        self.v_en = tk.BooleanVar(value=bool(vcfg.get("enabled", True)))
        ttk.Checkbutton(page_p, text="启用 火山引擎 Agent Plan",
                        variable=self.v_en).pack(anchor="w", pady=(0, 6))
        self.v_ak = tk.StringVar(value=vcfg.get("access_key_id", ""))
        self._secret_row(page_p, "AccessKey ID", self.v_ak,
                         hint="控制台 → API 访问密钥（AKLT 开头）")
        self.v_sk = tk.StringVar(value=vcfg.get("secret_access_key", ""))
        self._secret_row(page_p, "SecretAccessKey", self.v_sk,
                         hint="账号级密钥，非方舟模型 Key")
        self.v_region = tk.StringVar(value=vcfg.get("region", "cn-beijing"))
        self._entry_row(page_p, "地域", self.v_region,
                        hint="一般是 cn-beijing")

        # ================= 显示页 =================
        row = ttk.Frame(page_d); row.pack(fill="x", pady=4)
        ttk.Label(row, text="配色主题", width=14, anchor="w").pack(side="left")
        self.theme_choice = tk.StringVar(
            value=THEME_LABELS.get(str(cfg.get("theme", "auto") or "auto"),
                                   "自动（跟随背景）"))
        cb_theme = ttk.Combobox(row, textvariable=self.theme_choice, state="readonly",
                                values=list(THEME_LABELS.values()), width=16)
        cb_theme.pack(side="left")
        ttk.Label(page_d, text="自动模式会感应窗口背后的背景明暗",
                  foreground="#666").pack(anchor="w", padx=(98, 0))

        row = ttk.Frame(page_d); row.pack(fill="x", pady=(9, 0))
        ttk.Label(row, text="贴边收起", width=14, anchor="w").pack(side="left")
        self.edge_dock = tk.BooleanVar(value=bool(cfg.get("edge_dock", True)))
        tk.Checkbutton(row, text="拖到屏幕边缘自动收起，留进度小条，鼠标移上去弹出",
                       variable=self.edge_dock, bg="#f0f0f0", anchor="w"
                       ).pack(side="left")

        self.dock_len = tk.IntVar(
            value=min(240, max(40, int(cfg.get("dock_len", 70)))))
        self._spin_row(page_d, "长条长度", self.dock_len, 40, 240,
                       "px（贴边收起小条的长度）")

        scale_now = float(cfg.get("ui_scale", 1.0))
        nearest = min(SCALE_STEPS, key=lambda s: abs(s - scale_now))
        self.scale_var = tk.StringVar(value=SCALE_LABELS[nearest])
        row = ttk.Frame(page_d); row.pack(fill="x", pady=4)
        ttk.Label(row, text="界面缩放", width=14, anchor="w").pack(side="left")
        cb = ttk.Combobox(row, textvariable=self.scale_var, state="readonly",
                          values=[SCALE_LABELS[s] for s in SCALE_STEPS], width=16)
        cb.pack(side="left")
        ttk.Label(page_d, text="觉得浮窗太大/太小就调这里，保存后立即生效",
                  foreground="#666").pack(anchor="w", padx=(98, 0))

        row = ttk.Frame(page_d); row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="窗口透明度", width=14, anchor="w").pack(side="left")
        self.opacity = tk.IntVar(
            value=max(60, int(round(float(cfg.get("opacity", 0.96)) * 100))))
        sc = tk.Scale(row, from_=60, to=100, orient="horizontal", variable=self.opacity,
                      length=180, showvalue=True, resolution=1, bg="#f0f0f0",
                      highlightthickness=0, relief="flat")
        sc.pack(side="left")

        self.refresh = tk.IntVar(value=max(1, int(cfg.get("refresh_minutes", 5))))
        self._spin_row(page_d, "自动刷新间隔", self.refresh, 1, 120, "分钟")

        self.warn = tk.IntVar(value=int(cfg.get("warn_percent", 80)))
        self._spin_row(page_d, "黄色提醒阈值", self.warn, 10, 98, "%")

        self.crit = tk.IntVar(value=int(cfg.get("critical_percent", 95)))
        self._spin_row(page_d, "红色临界阈值", self.crit, 11, 99, "%")

        ttk.Separator(page_d).pack(fill="x", pady=12)
        row = ttk.Frame(page_d); row.pack(fill="x")
        ttk.Label(row, text="开机自启", width=14, anchor="w").pack(side="left")
        ttk.Button(row, text="设置", width=8,
                   command=lambda: self.on_autostart[0]()).pack(side="left", padx=2)
        ttk.Button(row, text="取消", width=8,
                   command=lambda: self.on_autostart[1]()).pack(side="left", padx=2)

        # ================= 底部按钮 =================
        bar = ttk.Frame(self, padding=(12, 6, 12, 12))
        bar.pack(fill="x")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bar, text="保存并应用", command=self._save).pack(side="right", padx=4)

        # 屏幕居中弹出
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        self.grab_set()
        self.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())

    # ---------------- 控件构造辅助 ----------------

    def _entry_row(self, parent, label, var, hint=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=14, anchor="w").pack(side="left")
        e = ttk.Entry(row, textvariable=var)
        e.pack(side="left", fill="x", expand=True, padx=(4, 0))
        if hint:
            ttk.Label(parent, text=hint, foreground="#888").pack(
                anchor="w", padx=(98, 0))
        return e

    def _secret_row(self, parent, label, var, hint=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=14, anchor="w").pack(side="left")
        e = ttk.Entry(row, textvariable=var, show="*")
        e.pack(side="left", fill="x", expand=True, padx=(4, 4))
        btn = tk.Button(row, text="显示", width=4, relief="groove",
                        bg="#f0f0f0", activebackground="#e8e8e8",
                        padx=4, pady=0)

        def _toggle():
            if e.cget("show") == "*":
                e.config(show="")
                btn.config(text="隐藏")
            else:
                e.config(show="*")
                btn.config(text="显示")
        btn.config(command=_toggle)
        btn.pack(side="left")
        if hint:
            ttk.Label(parent, text=hint, foreground="#888").pack(
                anchor="w", padx=(98, 0))
        return e

    def _spin_row(self, parent, label, var, lo, hi, unit):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=14, anchor="w").pack(side="left")
        sp = ttk.Spinbox(row, from_=lo, to=hi, increment=1, width=6,
                         textvariable=var)
        sp.pack(side="left", padx=(4, 6))
        ttk.Label(row, text=unit, foreground="#666").pack(side="left")

    # ---------------- 保存 ----------------

    def _save(self):
        label2scale = {SCALE_LABELS[s]: s for s in SCALE_STEPS}
        scale_val = label2scale.get(self.scale_var.get(), 1.0)
        warn = min(98, max(10, int(self.warn.get())))
        crit = min(99, max(11, int(self.crit.get())))
        if warn >= crit:  # 保证黄 < 红
            warn, crit = crit - 1, crit
        patch = {
            "zhipu": {
                "enabled": bool(self.z_en.get()),
                "api_key": self.z_key.get().strip(),
                "base_url": self.z_base.get().strip() or "open.bigmodel.cn",
            },
            "volcano": {
                "enabled": bool(self.v_en.get()),
                "access_key_id": self.v_ak.get().strip(),
                "secret_access_key": self.v_sk.get().strip(),
                "region": self.v_region.get().strip() or "cn-beijing",
            },
            "theme": {v: k for k, v in THEME_LABELS.items()}.get(
                self.theme_choice.get(), "auto"),
            "edge_dock": bool(self.edge_dock.get()),
            "dock_len": min(240, max(40, int(self.dock_len.get()))),
            "ui_scale": scale_val,
            "opacity": min(1.0, max(0.6, int(self.opacity.get()) / 100.0)),
            "refresh_minutes": min(120, max(1, int(self.refresh.get()))),
            "warn_percent": warn,
            "critical_percent": crit,
        }
        self.on_apply(patch)
        self.destroy()


# --------------------------------------------------------------------------
# 悬浮窗主程序
# --------------------------------------------------------------------------

class UsageWidget:

    def __init__(self, root, dpi=96):
        self.root = root
        self.dpi = dpi
        self.cfg = load_config()
        self.data = {"zhipu": None, "volcano": None}
        self.errors = {"zhipu": "", "volcano": ""}
        self.last_ok_time = None
        self.updating = False
        self._placed = False
        self._drag_offset = None
        self._moved = False

        # ---- 贴边收起状态 ----
        # dock_state: none(自由) / docked(收起) / expanded(贴边展开) / expanding(展开动画中)
        self.dock_state = "none"
        self.dock_side = str(self.cfg.get("dock_side", "none") or "none")
        self._animating = False
        self._anim_job = None
        self._collapse_timer = None
        self._suppress_collapse = False
        # ---- 详情面板倒计时刷新 ----
        self._detail_tick_job = None
        # ---- 配色主题 ----
        self.theme_eff = "dark"
        self.t = dict(THEMES["dark"])
        # 当前正常（展开）状态窗口尺寸
        self.cur_w = 0
        self.cur_h = 0

        # 启动时恢复上次的贴边收起状态
        if (self.cfg.get("edge_dock", True)
                and self.dock_side in ("left", "right", "top", "bottom")):
            self.dock_state = "docked"

        self._metrics()
        self._pick_font()
        # 菜单单选项（右键菜单贴边开关 / 配色主题）
        self.dock_var = tk.BooleanVar(value=bool(self.cfg.get("edge_dock", True)))
        self.theme_var = tk.StringVar(value=str(self.cfg.get("theme", "auto") or "auto"))
        self._build_window()
        self._build_menu()
        self._bind_events()

        self._apply_theme(force=True)
        self.redraw()
        if self._needs_setup():
            self.status_text = "未配置，右键打开设置"
            self.redraw()
            self.root.after(500, self.open_settings)
        self.root.after(300, self.refresh_async)

    # ---------------- 尺寸与字体 ----------------

    def _metrics(self):
        us = float(self.cfg.get("ui_scale", 1.0))
        us = min(1.3, max(0.75, us))
        self.uscale = us
        self.k = us * self.dpi / 96.0          # 总像素缩放因子
        k = self.k
        self.bar_h = max(4, int(5 * self.k))
        self.radius = max(7, int(10 * k))
        # 字号（point；dpi 部分由 tk scaling 处理，这里只乘用户缩放）
        self.fs_title = max(7, int(round(9 * us)))
        self.fs_text = max(7, int(round(9 * us)))
        self.fs_small = max(6, int(round(7 * us)))

    def _pick_font(self):
        available = set(tkfont.families(self.root))
        self.font_family = "TkDefaultFont"
        for f in FONT_FAMILY_CANDIDATES:
            if f in available:
                self.font_family = f
                break
        self.f_title = tkfont.Font(family=self.font_family,
                                   size=self.fs_title, weight="bold")
        self.f_text = tkfont.Font(family=self.font_family, size=self.fs_text)
        self.f_small = tkfont.Font(family=self.font_family, size=self.fs_small)

    # 简单别名，保持内部调用统一
    _pick_fonts = _pick_font

    def _build_window(self):
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)          # 无边框
        self.root.attributes("-topmost", True)     # 常驻置顶
        try:
            self.root.attributes("-alpha", float(self.cfg.get("opacity", 0.96)))
        except Exception:
            pass
        try:
            # Windows 下用透明色实现圆角外形；失败则退化为直角
            self.root.attributes("-transparentcolor", C_BG)
            self.transparent = True
        except Exception:
            self.transparent = False

        self.canvas = tk.Canvas(self.root, bg=C_BG, highlightthickness=0,
                                bd=0, width=120, height=80)
        self.canvas.pack(fill="both", expand=True)
        self.root.resizable(False, False)

    def _build_menu(self):
        try:
            self.menu.destroy()
        except Exception:
            pass
        # 菜单变量与最新配置同步
        self.dock_var.set(bool(self.cfg.get("edge_dock", True)))
        self.theme_var.set(str(self.cfg.get("theme", "auto") or "auto"))
        self.menu = tk.Menu(self.root, tearoff=0,
                            font=(self.font_family, 9), bd=1, relief="solid")
        self.menu.add_command(label="立即刷新", command=self.refresh_async)
        self.menu.add_command(label="设置…", command=self.open_settings)
        self.menu.add_separator()
        theme_menu = tk.Menu(self.menu, tearoff=0, font=(self.font_family, 9))
        for value, label in (("auto", "自动（跟随背景）"),
                             ("dark", "深色"), ("light", "浅色")):
            theme_menu.add_radiobutton(
                label=label, value=value, variable=self.theme_var,
                command=lambda v=value: self._set_theme(v))
        self.menu.add_cascade(label="配色主题", menu=theme_menu)
        self.menu.add_checkbutton(label="贴边自动收起", variable=self.dock_var,
                                  command=self._toggle_edge_dock)
        self.menu.add_separator()
        self.auto_menu = tk.Menu(self.menu, tearoff=0,
                                 font=(self.font_family, 9))
        self.auto_menu.add_command(label="设置开机自启", command=self.enable_autostart)
        self.auto_menu.add_command(label="取消开机自启", command=self.disable_autostart)
        self.menu.add_cascade(label="开机自启", menu=self.auto_menu)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)

    def _toggle_edge_dock(self):
        """开/关贴边自动收起。关闭时若正处于收起状态则恢复普通窗口。"""
        val = bool(self.dock_var.get())
        self.cfg["edge_dock"] = val
        save_config_patch({"edge_dock": val})
        if not val and self.dock_state == "docked":
            self._snap_to_expanded()  # 直接恢复普通窗口（不会再自动收回）

    def _bind_events(self):
        c = self.canvas
        c.bind("<Button-1>", self._on_drag_start)
        c.bind("<B1-Motion>", self._on_drag_motion)
        c.bind("<ButtonRelease-1>", self._on_drag_end)
        c.bind("<Button-3>", self._on_right_click)   # Windows 右键
        c.bind("<Button-2>", self._on_right_click)   # macOS 右键
        c.bind("<Enter>", self._on_widget_enter)     # 悬停：贴边展开 / 圆环详情
        c.bind("<Leave>", self._on_widget_leave)     # 离开：立即收回

    # ---------------- 拖动 / 菜单 ----------------

    def _on_drag_start(self, event):
        self._cancel_collapse_timer()
        self._cancel_detail_tick()
        # 从贴边收起/展开状态开始拖动：先瞬间恢复成正常窗口（贴边展开位）
        snap_pos = None
        if self.dock_state != "none":
            snap_pos = self._snap_to_expanded()
        # 绝对定位锚点：鼠标屏幕坐标 - 窗口坐标
        # （snap 后 winfo 尚未刷新，用返回的精确位置）
        if snap_pos is not None:
            wx, wy = snap_pos
        else:
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
        self._drag_offset = (event.x_root - wx, event.y_root - wy)
        self._drag_origin = (wx, wy)
        self._moved = False
        self._drag_pending = None

    def _on_drag_motion(self, event):
        if not self._drag_offset:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        ox, oy = self._drag_origin
        if abs(x - ox) + abs(y - oy) > 3:
            self._moved = True
        # 节流：合并高频鼠标事件，约每 10ms 应用一次
        self._drag_pending = (x, y)
        if not getattr(self, "_drag_scheduled", False):
            self._drag_scheduled = True
            self.root.after(10, self._apply_drag_move)

    def _apply_drag_move(self):
        self._drag_scheduled = False
        if self._drag_pending and self._drag_offset:
            x, y = self._drag_pending
            self._drag_pending = None
            self.root.geometry("+%d+%d" % (x, y))

    def _on_drag_end(self, event):
        frm = None
        if self._drag_pending:
            frm = self._drag_pending
            self._drag_pending = None
            self.root.geometry("+%d+%d" % frm)
        self._drag_offset = None
        if self._moved:
            # 拖动结束时检测是否贴近屏幕边缘 → 自动收起
            side = self._detect_edge() if self.cfg.get("edge_dock", True) else None
            if side:
                self._dock_to(side, frm=frm)
                return
            if self.dock_side != "none":
                self.dock_side = "none"
                save_config_patch({"dock_side": "none"})
            self._save_window_pos()
            if self._apply_theme():   # 拖到了明暗不同的背景上，自动换主题
                self.redraw()

    def _on_right_click(self, event):
        self._cancel_collapse_timer()
        # 右键菜单打开期间不要自动收回（指针移到菜单上会触发 Leave）
        self._suppress_collapse = True
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _save_window_pos(self):
        save_config_patch({"window_x": self.root.winfo_x(),
                           "window_y": self.root.winfo_y()})

    # ---------------- 配色主题 ----------------

    def _theme_sample_rect(self):
        """返回用于背景取样的矩形 (x, y, w, h)。"""
        w, h = self._compute_layout_size()
        if self._placed:
            x, y = self.root.winfo_x(), self.root.winfo_y()
        else:
            x = int(self.cfg.get("window_x", 120))
            y = int(self.cfg.get("window_y", 120))
        # 贴边收起时窗口大半在屏幕外，改用贴边展开位取样
        if self.dock_state == "docked":
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            if self.dock_side == "left":
                x = 0
            elif self.dock_side == "right":
                x = sw - w
            elif self.dock_side == "top":
                y = 0
            elif self.dock_side == "bottom":
                y = sh - h
        return x, y, w, h

    def _apply_theme(self, force=False):
        """按配置（含自动感应）刷新主题；返回主题是否发生变化。"""
        mode = str(self.cfg.get("theme", "auto") or "auto")
        eff = mode
        if mode == "auto":
            try:
                x, y, w, h = self._theme_sample_rect()
                lum = sample_background_luminance(
                    x, y, w, h,
                    self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            except Exception:
                lum = None
            eff = "light" if (lum is not None and lum >= 150) else "dark"
        changed = (eff != self.theme_eff) or force
        self.theme_eff = eff
        self.t = THEMES[eff]
        return changed

    def _set_theme(self, mode):
        """右键菜单切换配色主题，立即生效并保存。"""
        self.cfg["theme"] = mode
        save_config_patch({"theme": mode})
        self.theme_var.set(mode)
        self._apply_theme(force=True)
        self.redraw()

    # ---------------- 贴边自动收起 ----------------

    def _restore_alpha(self):
        try:
            self.root.attributes("-alpha", float(self.cfg.get("opacity", 0.96)))
        except Exception:
            pass

    def _cancel_collapse_timer(self):
        if self._collapse_timer is not None:
            try:
                self.root.after_cancel(self._collapse_timer)
            except Exception:
                pass
            self._collapse_timer = None

    def _detect_edge(self, threshold=26):
        """返回当前窗口离哪条屏幕边最近（在阈值内），否则 None。"""
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        dists = {"left": x, "top": y,
                 "right": sw - (x + w), "bottom": sh - (y + h)}
        side = min(dists, key=dists.get)
        return side if dists[side] <= threshold else None

    def _animate_move(self, tx, ty, done=None, frm=None):
        """缓出动画移动窗口到 (tx, ty)，结束回调 done。
        frm 可显式指定起点：geometry() 请求后 winfo 立即读仍是旧值。"""
        if self._anim_job is not None:
            try:
                self.root.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        if frm is not None:
            sx, sy = frm
        else:
            self.root.update_idletasks()
            sx, sy = self.root.winfo_x(), self.root.winfo_y()
        dx, dy = tx - sx, ty - sy
        if abs(dx) < 1 and abs(dy) < 1:
            if done:
                done()
            return
        dist = max(abs(dx), abs(dy))
        steps = max(4, min(16, int(dist / 22)))

        def step(i):
            t = float(i + 1) / steps
            ease = 1.0 - (1.0 - t) ** 3  # ease-out cubic
            self.root.geometry("+%d+%d" % (
                int(round(sx + dx * ease)), int(round(sy + dy * ease))))
            if i + 1 < steps:
                self._anim_job = self.root.after(12, step, i + 1)
            else:
                self._anim_job = None
                if done:
                    done()
        step(0)

    def _snap_to_expanded(self):
        """从贴边状态瞬间恢复为正常窗口（贴边展开位），返回新位置 (x, y)。"""
        self._cancel_collapse_timer()
        self._cancel_detail_tick()
        side = self.dock_side
        w, h = self._compute_layout_size()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        # 与悬停展开同一位置规则：沿把手中心恢复为面板
        cx = x + self.root.winfo_width() // 2
        cy = y + self.root.winfo_height() // 2
        if side == "left":
            x, y = 0, min(max(0, cy - h // 2), max(0, sh - h))
        elif side == "right":
            x, y = sw - w, min(max(0, cy - h // 2), max(0, sh - h))
        elif side == "top":
            x, y = min(max(0, cx - w // 2), max(0, sw - w)), 0
        elif side == "bottom":
            x, y = min(max(0, cx - w // 2), max(0, sw - w)), sh - h
        self.dock_state = "none"
        self._placed = True
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.redraw()
        return x, y

    def _dock_to(self, side, frm=None):
        """滑入收起到指定边，之后由把手持续显示微型进度条。"""
        self._cancel_collapse_timer()
        self.dock_side = side
        self._animating = True
        save_config_patch({"dock_side": side})
        w, h = self.cur_w, self.cur_h
        if frm is None:
            self.root.update_idletasks()
            frm = (self.root.winfo_x(), self.root.winfo_y())
        x, y = frm
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        hw, hl = self._handle_size()
        # 只沿贴边方向直线滑出（另一坐标不动），滑到屏幕外后再对齐到
        # 把手原位（面板中心处）重绘；全程直线，无斜向漂移
        if side == "left":
            tx, ty = -w + 1, y
            hy = min(max(0, y + h // 2 - hl // 2), max(0, sh - hl))
        elif side == "right":
            tx, ty = sw - 1, y
            hy = min(max(0, y + h // 2 - hl // 2), max(0, sh - hl))
        elif side == "top":
            tx, ty = x, -h + 1
            hx = min(max(0, x + w // 2 - hw // 2), max(0, sw - hw))
        else:
            tx, ty = x, sh - 1
            hx = min(max(0, x + w // 2 - hw // 2), max(0, sw - hw))

        def done():
            self._animating = False
            self.dock_state = "docked"
            # 窗口已在屏幕外（仅剩 1px 透明边），先把把手方向坐标调到原位
            # （发生在屏幕外看不见），再重绘成把手
            if side == "left":
                self._handle_home = (0, hy)
                self.root.geometry("+%d+%d" % (-w + 1, hy))
            elif side == "right":
                self._handle_home = (sw - hw, hy)
                self.root.geometry("+%d+%d" % (sw - 1, hy))
            elif side == "top":
                self._handle_home = (hx, 0)
                self.root.geometry("+%d+%d" % (hx, -h + 1))
            else:
                self._handle_home = (hx, sh - hl)
                self.root.geometry("+%d+%d" % (hx, sh - 1))
            self.root.update_idletasks()
            self._apply_theme()
            self.redraw()          # 重绘为把手（含微型进度条）
            self.root.update_idletasks()
            self._save_window_pos()

        self._animate_move(tx, ty, done, frm=frm)

    def _expand_from_dock(self):
        """从收起状态滑出展开（鼠标移上把手时触发），显示完整详情面板。"""
        if self._animating or self.dock_state != "docked":
            return
        self._cancel_collapse_timer()
        side = self.dock_side
        w, h = self._compute_layout_size()   # 详情面板尺寸
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        # 面板沿把手中心展开（左右贴边垂直居中、上下贴边水平居中），
        # 指针始终落在展开后的面板内，不会触发误收回
        cx = x + self.root.winfo_width() // 2
        cy = y + self.root.winfo_height() // 2
        if side == "left":
            y = min(max(0, cy - h // 2), max(0, sh - h))
            off, on = (-w + 1, y), (0, y)
        elif side == "right":
            y = min(max(0, cy - h // 2), max(0, sh - h))
            off, on = (sw - 1, y), (sw - w, y)
        elif side == "top":
            x = min(max(0, cx - w // 2), max(0, sw - w))
            off, on = (x, -h + 1), (x, 0)
        else:
            x = min(max(0, cx - w // 2), max(0, sw - w))
            off, on = (x, sh - 1), (x, sh - h)
        self._animating = True
        # 先在屏幕外恢复成详情面板尺寸与内容，再滑入（切换发生在屏幕外，无闪烁）
        self.root.geometry("%dx%d+%d+%d" % (w, h, off[0], off[1]))
        self.dock_state = "expanding"
        self.redraw()

        def done():
            self._animating = False
            self.dock_state = "expanded"

        self._animate_move(on[0], on[1], done, frm=off)
        self._start_detail_tick()
        # 数据超过 2 分钟未刷新：展开时顺带拉取一次最新额度
        if (self.last_ok_time is None or
                (datetime.datetime.now() - self.last_ok_time).total_seconds() > 120):
            self.refresh_async()

    def _collapse_to_dock(self):
        """贴边展开状态下鼠标离开后，直线滑回收起。"""
        self._collapse_timer = None
        self._cancel_detail_tick()
        if self._animating or self.dock_state != "expanded":
            return
        if not self.cfg.get("edge_dock", True):
            self.dock_state = "none"
            return
        if self._suppress_collapse:
            return
        side = self.dock_side
        w, h = self.cur_w, self.cur_h
        x, y = self.root.winfo_x(), self.root.winfo_y()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        hw, hl = self._handle_size()
        # 把手原位：优先用展开时记下的位置（展开被屏幕夹取时也能原位收回），
        # 没有记录再按面板中心推算
        home = getattr(self, "_handle_home", None)
        if home is None:
            home = (x + w // 2 - hw // 2, y + h // 2 - hl // 2)
        # 只沿贴边方向直线滑出（另一坐标不动），与展开动画对称；
        # 对齐到把手原位的一步放在屏幕外做，看不见
        if side == "left":
            slide = (-w + 1, y)
            hy = min(max(0, home[1]), max(0, sh - hl))
        elif side == "right":
            slide = (sw - 1, y)
            hy = min(max(0, home[1]), max(0, sh - hl))
        elif side == "top":
            slide = (x, -h + 1)
            hx = min(max(0, home[0]), max(0, sw - hw))
        else:
            slide = (x, sh - 1)
            hx = min(max(0, home[0]), max(0, sw - hw))
        self._animating = True

        def done():
            self._animating = False
            self.dock_state = "docked"
            # 窗口已在屏幕外（仅剩 1px 透明边），先把把手方向坐标调回原位，
            # 再重绘成把手 —— 收回全程直线滑出，不再斜向漂移
            if side in ("left", "right"):
                self.root.geometry("+%d+%d" % (slide[0], hy))
            else:
                self.root.geometry("+%d+%d" % (hx, slide[1]))
            self.root.update_idletasks()
            self._apply_theme()
            self.redraw()

        self._animate_move(slide[0], slide[1], done)

    def _on_widget_enter(self, event):
        self._suppress_collapse = False
        if self.dock_state == "docked" and not self._animating:
            if self.cfg.get("edge_dock", True):
                self._expand_from_dock()   # 展开完整详情面板
            else:
                self._snap_to_expanded()   # 开关已关（手改配置等）：恢复普通窗口
            return

    def _pointer_in_window(self, px, py, tol=2):
        """指针坐标是否仍在窗口范围内（含贴屏幕边缘时的 ±tol 容差）。"""
        wx, wy = self.root.winfo_x(), self.root.winfo_y()
        ww, wh = self.root.winfo_width(), self.root.winfo_height()
        return (wx - tol <= px <= wx + ww + tol - 1 and
                wy - tol <= py <= wy + wh + tol - 1)

    def _recheck_pointer(self):
        """Leave 误报后的兜底轮询：指针真离开了就收回，防卡在展开态。"""
        self._collapse_timer = None
        if self.dock_state != "expanded" or self._animating:
            return
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
            inside = self._pointer_in_window(px, py)
        except Exception:
            inside = False
        if inside or self._suppress_collapse:
            # 仍贴在窗口上（或菜单打开期间）：继续盯
            self._collapse_timer = self.root.after(300, self._recheck_pointer)
        else:
            self._collapse_to_dock()

    def _on_widget_leave(self, event):
        if self.dock_state == "expanded" and not self._animating:
            if self.cfg.get("edge_dock", True):
                # 面板边线与屏幕边缘重合时，指针顶到屏幕最边缘会被系统
                # 判成出界而误报 Leave；按指针实际位置复核
                try:
                    px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
                    if self._pointer_in_window(px, py):
                        self._cancel_collapse_timer()
                        self._collapse_timer = self.root.after(
                            300, self._recheck_pointer)
                        return
                except Exception:
                    pass
                self._cancel_collapse_timer()
                self._collapse_to_dock()   # 鼠标移开立即收回，不做延时
            else:
                self._snap_to_expanded()   # 开关已关：直接恢复普通窗口

    def _clamp_start_pos(self):
        """窗口记忆位置跑出屏幕（改分辨率/换显示器）时复位。"""
        x = int(self.cfg.get("window_x", 120))
        y = int(self.cfg.get("window_y", 120))
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = int(300 * self.k)   # 详情面板宽度
            if x > sw - 60 or y > sh - 60 or x < 40 - w or y < 0:
                return 120, 120
        except Exception:
            pass
        return x, y

    # ---------------- 设置 ----------------

    def _needs_setup(self):
        z = self.cfg.get("zhipu", {})
        v = self.cfg.get("volcano", {})
        return is_placeholder(z.get("api_key")) and is_placeholder(v.get("access_key_id"))

    def open_settings(self):
        try:
            if self._settings_open:
                self._settings_open.lift()
                self._settings_open.focus_force()
                return
        except Exception:
            pass
        self._settings_open = SettingsDialog(
            self.root, self.cfg, self.apply_settings,
            (self.enable_autostart, self.disable_autostart))

    def apply_settings(self, patch):
        save_config_patch(patch)             # 增量写盘
        _deep_merge(self.cfg, patch)         # 同步内存
        self._restore_alpha()
        self._metrics()
        self._pick_font()
        self._build_menu()
        self._apply_theme()                  # 配色主题可能被修改
        self._placed = True                  # 保持当前位置，不跳回
        if not self.cfg.get("edge_dock", True) and self.dock_state == "docked":
            self._snap_to_expanded()       # 贴边开关被关闭：恢复普通窗口
        else:
            self.redraw()
        self.refresh_async()

    # ---------------- 开机自启 ----------------

    def _autostart_file(self):
        if os.name != "nt":
            return None
        base = os.path.join(os.environ.get("APPDATA", ""),
                            "Microsoft", "Windows", "Start Menu",
                            "Programs", "Startup")
        return os.path.join(base, "usage_widget.vbs")

    def enable_autostart(self):
        path = self._autostart_file()
        if not path:
            self._toast("开机自启仅支持 Windows")
            return
        if getattr(sys, "frozen", False):
            # 打包版：直接启动 EXE。整条命令包在一对引号里，
            # 内部路径的引号用两个连续引号转义，避免 VBScript 语法错误
            cmd = '""%s""' % os.path.abspath(sys.executable)
        else:
            script = os.path.abspath(__file__)
            exe = sys.executable
            # 若当前由 python.exe 启动，换用 pythonw.exe，避免自启时弹出控制台
            if os.path.basename(exe).lower() == "python.exe":
                pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
                if os.path.exists(pw):
                    exe = pw
            cmd = '""%s"" ""%s""' % (exe, script)
        content = 'CreateObject("WScript.Shell").Run "%s", 0, False\n' % cmd
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                # WScript 按系统 ANSI(GBK) 读取 .vbs，二进制写入避免中文路径乱码
                f.write(content.encode("gbk", "replace"))
            self._toast("已设置开机自启")
        except Exception as e:
            self._toast("设置失败: %s" % e)

    def disable_autostart(self):
        path = self._autostart_file()
        if path and os.path.exists(path):
            try:
                os.remove(path)
                self._toast("已取消开机自启")
            except Exception as e:
                self._toast("取消失败: %s" % e)
        else:
            self._toast("当前没有开机自启")

    def quit(self):
        self._cancel_collapse_timer()
        self._cancel_detail_tick()
        if self._anim_job is not None:
            try:
                self.root.after_cancel(self._anim_job)
            except Exception:
                pass
        self._save_window_pos()
        self.root.destroy()

    # ---------------- 数据刷新 ----------------

    def refresh_async(self):
        if self.updating:
            return
        self.updating = True
        self.status_text = "更新中…"
        self.redraw()

        def worker():
            zr, vr = None, None
            # 每次刷新前重读磁盘配置：手动编辑 config.json 保存后，
            # 右键「立即刷新」即可生效，无需重启程序
            try:
                _deep_merge(self.cfg, load_config())
            except Exception:
                pass
            zcfg, vcfg = self.cfg.get("zhipu", {}), self.cfg.get("volcano", {})
            if zcfg.get("enabled", True):
                try:
                    zr = fetch_zhipu(zcfg)
                except Exception as e:
                    zr = {"ok": False, "error": str(e)[:80]}
            if vcfg.get("enabled", True):
                try:
                    vr = fetch_volcano(vcfg)
                except Exception as e:
                    vr = {"ok": False, "error": str(e)[:80]}
            self.root.after(0, lambda: self._apply_refresh(zr, vr))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_refresh(self, zr, vr):
        self.updating = False
        self._apply_visual_changes()
        self._apply_theme()      # 定时重估背景明暗（壁纸轮换等场景）
        now = datetime.datetime.now()
        any_ok = False
        if zr is not None:
            self.errors["zhipu"] = "" if zr.get("ok") else zr.get("error", "失败")
            if zr.get("ok"):
                self.data["zhipu"] = zr
                any_ok = True
        if vr is not None:
            self.errors["volcano"] = "" if vr.get("ok") else vr.get("error", "失败")
            if vr.get("ok"):
                self.data["volcano"] = vr
                any_ok = True
        if any_ok:
            self.last_ok_time = now
            self.status_text = "更新于 %s" % now.strftime("%H:%M")
        else:
            err = self.errors.get("zhipu") or self.errors.get("volcano") or "失败"
            self.status_text = "更新失败：%s" % err[:22]
        self.redraw()
        minutes = max(1, int(self.cfg.get("refresh_minutes", 5)))
        self.root.after(minutes * 60 * 1000, self.refresh_async)

    # ---------------- 绘制 ----------------

    def _apply_visual_changes(self):
        """配置被外部修改（手改 config.json 后刷新）时同步视觉参数。"""
        try:
            if abs(float(self.cfg.get("ui_scale", 1.0)) - self.uscale) > 1e-6:
                self._metrics()
                self._pick_font()
                self._build_menu()
            self.root.attributes("-alpha", float(self.cfg.get("opacity", 0.96)))
        except Exception:
            pass

    def _color(self, percent):
        if percent is None:
            return self.t["dim"]
        try:
            percent = float(percent)
        except Exception:
            return self.t["dim"]
        if percent >= float(self.cfg.get("critical_percent", 95)):
            return self.t["crit"]
        if percent >= float(self.cfg.get("warn_percent", 80)):
            return self.t["warn"]
        return self.t["ok"]

    def _round_rect(self, x0, y0, x1, y1, r, canvas=None, **kw):
        cv = canvas if canvas is not None else self.canvas
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
               x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return cv.create_polygon(pts, smooth=True, **kw)

    def _tag_bg(self, accent):
        if accent == C_ACCENT_Z:
            return self.t["tag_z"]
        if accent == C_ACCENT_V:
            return self.t["tag_v"]
        return self.t["track"]

    def redraw(self):
        """总入口：贴边收起时画把手，其余状态都是完整详情面板。"""
        if self.dock_state == "docked":
            self._redraw_handle()
        else:
            self._redraw_detail()

    # ---------------- 数据聚合（把手 / 圆环共用） ----------------

    def _progress_groups(self):
        """返回 [(供应商名, 品牌色, [(进度名, 百分比), ...]), ...]，只含已启用的。"""
        groups = []
        if self.cfg.get("zhipu", {}).get("enabled", True):
            zd = self.data.get("zhipu")
            if zd and zd.get("ok"):
                groups.append(("智谱", C_ACCENT_Z,
                               [("5小时", zd.get("five_hour")),
                                ("本周", zd.get("weekly"))]))
            else:
                groups.append(("智谱", C_ACCENT_Z, []))
        if self.cfg.get("volcano", {}).get("enabled", True):
            vd = self.data.get("volcano")
            if vd and vd.get("ok"):
                wins = (vd.get("windows") or [])[:2]
                groups.append(("火山", C_ACCENT_V, [
                    (nm, round(u * 100.0 / q, 1) if q else None)
                    for nm, u, q, _ in wins]))
            else:
                groups.append(("火山", C_ACCENT_V, []))
        return groups

    def _compute_layout_size(self):
        """计算详情面板（正常/贴边展开状态）窗口尺寸，不绘制。"""
        return self._detail_layout()[:2]

    # ---------------- 详情面板（贴边悬停展开） ----------------

    def _detail_layout(self):
        """构建详情面板内容块，返回 (w, h, blocks, pad)。
        blocks 高度含全部间距，绘制时按顺序消费即可保证与总高一致。"""
        k = self.k
        w = int(300 * k)
        pad = int(12 * k)
        label_h = max(16, int(18 * k))
        line_h = max(14, int(15 * k))
        det_h = max(13, int(14 * k))
        win_h = label_h + self.bar_h + int(3 * k) + line_h + int(9 * k)

        blocks = [{"t": "head", "h": max(20, int(24 * k)) + int(6 * k)}]
        z_on = self.cfg.get("zhipu", {}).get("enabled", True)
        v_on = self.cfg.get("volcano", {}).get("enabled", True)

        def prov(name, accent, level):
            blocks.append({"t": "prov", "name": name, "accent": accent,
                           "level": (level or "").upper(), "h": max(20, int(24 * k))})

        if z_on:
            zd = self.data.get("zhipu") or {}
            prov("智谱 Coding Plan", C_ACCENT_Z, zd.get("level"))
            if zd.get("ok"):
                wins = zd.get("windows") or []
                if not wins:   # 旧结构数据兜底
                    wins = [("5小时", zd.get("five_hour"), None),
                            ("本周", zd.get("weekly"), None)]
                for nm, pct, rst in wins[:2]:
                    blocks.append({"t": "win",
                                   "label": "5 小时窗口" if nm == "5小时" else "本周额度",
                                   "pct": pct, "reset": rst, "used": "",
                                   "h": win_h})
                mcp = zd.get("mcp")
                if mcp:
                    blocks.append({
                        "t": "win", "label": "MCP 联网工具 · 本月",
                        "pct": mcp.get("percent"), "reset": mcp.get("reset_ms"),
                        "used": "已用 %s / %s" % (
                            self._fmt_num(mcp.get("used", 0)),
                            self._fmt_num(mcp.get("total", 0))),
                        "h": win_h + det_h, "details": mcp.get("details") or []})
            else:
                blocks.append({"t": "err", "h": line_h + int(4 * k),
                               "text": "⚠ " + (self.errors.get("zhipu")
                                                or "暂无数据，右键 → 立即刷新")})

        if v_on:
            vd = self.data.get("volcano") or {}
            prov("火山 Agent Plan", C_ACCENT_V, vd.get("level"))
            if vd.get("ok"):
                for nm, used, quota, reset in (vd.get("windows") or [])[:3]:
                    pct = round(used * 100.0 / quota, 1) if quota else None
                    blocks.append({"t": "win", "label": str(nm) + "额度",
                                   "pct": pct, "reset": reset,
                                   "used": "已用 %s / %s" % (
                                       self._fmt_num(used), self._fmt_num(quota)),
                                   "h": win_h})
            else:
                blocks.append({"t": "err", "h": line_h + int(4 * k),
                               "text": "⚠ " + (self.errors.get("volcano")
                                                or "暂无数据，右键 → 立即刷新")})

        if not z_on and not v_on:
            blocks.append({"t": "err", "h": line_h + int(6 * k),
                           "text": "两个供应商均已停用（右键 → 设置）"})

        # 供应商分隔线（首个供应商除外，行距计入自身高度）
        seen_prov = False
        for b in blocks:
            if b["t"] == "prov":
                if seen_prov:
                    b["sep"] = True
                    b["h"] += int(13 * k)
                seen_prov = True
        blocks.append({"t": "foot", "h": int(4 * k) + max(16, int(20 * k))})

        h = pad * 2 + sum(b["h"] for b in blocks)
        return w, h, blocks, pad

    def _redraw_detail(self):
        """完整详情面板（主窗口 / 贴边悬停展开共用）：
        各窗口额度、重置时间、用量明细。"""
        c = self.canvas
        c.delete("all")
        w, h, blocks, pad = self._detail_layout()
        k = self.k
        self.cur_w, self.cur_h = w, h
        self._round_rect(2, 2, w - 2, h - 2, self.radius,
                         fill=self.t["card"], outline=self.t["line"])
        f_pct = tkfont.Font(family=self.font_family,
                            size=max(9, int(round(11 * self.uscale))),
                            weight="bold")

        for b in blocks:
            t = b["t"]
            if t == "head":
                cy = pad + b["h"] / 2.0 - int(3 * k)
                c.create_text(pad, cy, text="额度详情", anchor="w",
                              font=self.f_title, fill=self.t["text"])
                if self.updating:
                    right = "刷新中…"
                elif self.last_ok_time:
                    right = "更新于 " + self.last_ok_time.strftime("%H:%M:%S")
                else:
                    right = "尚未更新"
                c.create_text(w - pad, cy, text=right, anchor="e",
                              font=self.f_small, fill=self.t["dim"])
                ly = pad + b["h"] - int(6 * k)
                c.create_line(pad, ly, w - pad, ly, fill=self.t["line"], width=1)
                y = pad + b["h"]
            elif t == "prov":
                if b.get("sep"):
                    c.create_line(pad, y + int(5 * k), w - pad, y + int(5 * k),
                                  fill=self.t["line"], width=1)
                    y += int(13 * k)
                cy = y + b["h"] / 2.0
                dot_r = max(2, int(2.5 * k))
                c.create_oval(pad, cy - dot_r, pad + 2 * dot_r, cy + dot_r,
                              fill=b["accent"], width=0)
                tx = pad + 2 * dot_r + int(5 * k)
                c.create_text(tx, cy, text=b["name"], anchor="w",
                              font=self.f_title, fill=self.t["text"])
                if b["level"]:
                    lx = tx + self.f_title.measure(b["name"]) + int(6 * k)
                    lw = self.f_small.measure(b["level"]) + int(8 * k)
                    c.create_rectangle(lx, cy - int(8 * k), lx + lw, cy + int(8 * k),
                                       fill=self._tag_bg(b["accent"]), width=0)
                    c.create_text(lx + int(4 * k), cy, text=b["level"],
                                  anchor="w", font=self.f_small, fill=self.t["text"])
                y += b["h"]
            elif t == "win":
                label_h = max(16, int(18 * k))
                line_h = max(14, int(15 * k))
                c.create_text(pad, y + label_h / 2.0, text=b["label"], anchor="w",
                              font=self.f_text, fill=self.t["dim"])
                pct = b["pct"]
                pct_txt = "%d%%" % round(float(pct)) if pct is not None else "—"
                c.create_text(w - pad, y + label_h / 2.0, text=pct_txt,
                              anchor="e", font=f_pct, fill=self._color(pct))
                y += label_h
                # 进度条
                bh = self.bar_h
                self._round_rect(pad, y, w - pad, y + bh, bh / 2.0,
                                 fill=self.t["track"], outline=self.t["track"])
                if pct is not None and float(pct) > 0:
                    fw = (w - 2 * pad) * min(100.0, float(pct)) / 100.0
                    self._round_rect(pad, y, pad + fw, y + bh, bh / 2.0,
                                     fill=self._color(pct),
                                     outline=self._color(pct))
                y += bh + int(3 * k)
                # 用量 + 重置时间
                cd, abs_s = fmt_reset_info(b["reset"])
                seg = b["used"]
                for extra in (cd, abs_s):
                    if extra:
                        seg = (seg + " · " if seg else "") + extra
                c.create_text(pad, y + line_h / 2.0, text=seg or "—",
                              anchor="w", font=self.f_small, fill=self.t["dim"])
                y += line_h
                if b.get("details"):
                    det_h = max(13, int(14 * k))
                    dtxt = " · ".join("%s %s" % (nm, self._fmt_num(uv))
                                      for nm, uv in b["details"][:4])
                    c.create_text(pad, y + det_h / 2.0, text=dtxt, anchor="w",
                                  font=self.f_small, fill=self.t["dim"])
                    y += det_h
                y += int(9 * k)
            elif t == "err":
                c.create_text(pad, y + b["h"] / 2.0, text=b["text"][:34],
                              anchor="w", font=self.f_small, fill=self.t["warn"])
                y += b["h"]
            elif t == "foot":
                c.create_line(pad, y + int(2 * k), w - pad, y + int(2 * k),
                              fill=self.t["line"], width=1)
                cy = y + int(4 * k) + max(16, int(20 * k)) / 2.0
                rm = self.cfg.get("refresh_minutes", 5)
                c.create_text(pad, cy,
                              text="每 %d 分钟自动刷新 · 右键可立即刷新" % rm,
                              anchor="w", font=self.f_small, fill=self.t["dim"])

        c.configure(width=w, height=h)
        geo = "%dx%d" % (w, h)
        if not self._placed:
            x, y0 = self._clamp_start_pos()
            geo += "+%d+%d" % (x, y0)
            self._placed = True
        # 首次按配置定位，之后只调尺寸不重置位置（避免拖动后被刷新弹回；
        # 贴边展开/收回时位置由动画管理，同样只传尺寸）
        self.root.geometry(geo)

    def _start_detail_tick(self):
        """详情面板停留期间每 30 秒刷新倒计时文字。"""
        self._cancel_detail_tick()
        self._detail_tick_job = self.root.after(30000, self._detail_tick)

    def _detail_tick(self):
        self._detail_tick_job = None
        if self.dock_state in ("expanding", "expanded") and not self._animating:
            self.redraw()
            self._detail_tick_job = self.root.after(30000, self._detail_tick)

    def _cancel_detail_tick(self):
        if self._detail_tick_job is not None:
            try:
                self.root.after_cancel(self._detail_tick_job)
            except Exception:
                pass
            self._detail_tick_job = None

    # ---------------- 贴边把手：微型进度条 ----------------

    def _handle_size(self):
        """贴边把手的 (宽, 高)，_redraw_handle 与入坞/收回定位共用。"""
        k = self.k
        side = self.dock_side
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        t = max(7, int(12 * k))      # 露出厚度
        # 把手长度：设置「长条长度」可调（逻辑像素，随缩放走），默认 70，限 40~240
        try:
            dock_len = float(self.cfg.get("dock_len", 70))
        except Exception:
            dock_len = 70.0
        grip = int(min(240.0, max(40.0, dock_len)) * k)
        if side in ("left", "right"):
            return t, max(t * 4, min(grip, sh - 4))
        return max(t * 4, min(grip, sw - 4)), t

    def _redraw_handle(self):
        """贴边收起状态：边缘只留一条短把手，内嵌各供应商微型进度条。"""
        c = self.canvas
        c.delete("all")
        k = self.k
        side = self.dock_side
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        hw, hl = self._handle_size()

        # 胶囊形背景
        self._round_rect(0, 0, hw, hl, min(hw, hl) / 2.0,
                         fill=self.t["card"], outline=self.t["line"])
        groups = self._progress_groups()
        if not groups:
            c.create_oval(hw / 2 - 2 * k, hl / 2 - 2 * k,
                          hw / 2 + 2 * k, hl / 2 + 2 * k,
                          fill=self.t["track"], width=0)
        else:
            n = len(groups)
            vertical = side in ("left", "right")
            if vertical:
                pad_v, gap_v = int(5 * k), int(6 * k)
                seg = (hl - 2 * pad_v - gap_v * (n - 1)) / float(n)
                bar_w, bar_gap = max(2, int(3.5 * k)), max(1, int(2 * k))
                for gi, (_name, _accent, bars) in enumerate(groups):
                    y0 = pad_v + gi * (seg + gap_v)
                    slots = list(bars[:2])
                    while len(slots) < 2:
                        slots.append(None)
                    total_w = len(slots) * bar_w + (len(slots) - 1) * bar_gap
                    bx = (hw - total_w) / 2.0
                    for bi, item in enumerate(slots):
                        x0 = bx + bi * (bar_w + bar_gap)
                        c.create_rectangle(x0, y0, x0 + bar_w, y0 + seg,
                                           fill=self.t["track"], width=0)
                        if item and item[1] is not None:
                            try:
                                pct = max(0.0, min(100.0, float(item[1])))
                            except Exception:
                                continue
                            ph = seg * pct / 100.0
                            if ph >= 1:
                                c.create_rectangle(
                                    x0, y0 + seg - ph, x0 + bar_w, y0 + seg,
                                    fill=self._color(pct), width=0)
            else:
                pad_h, gap_h = int(6 * k), int(6 * k)
                seg = (hw - 2 * pad_h - gap_h * (n - 1)) / float(n)
                bar_h, bar_gap = max(2, int(3.5 * k)), max(1, int(2 * k))
                for gi, (_name, _accent, bars) in enumerate(groups):
                    x0 = pad_h + gi * (seg + gap_h)
                    slots = list(bars[:2])
                    while len(slots) < 2:
                        slots.append(None)
                    total_hh = len(slots) * bar_h + (len(slots) - 1) * bar_gap
                    by = (hl - total_hh) / 2.0
                    for bi, item in enumerate(slots):
                        yy = by + bi * (bar_h + bar_gap)
                        c.create_rectangle(x0, yy, x0 + seg, yy + bar_h,
                                           fill=self.t["track"], width=0)
                        if item and item[1] is not None:
                            try:
                                pct = max(0.0, min(100.0, float(item[1])))
                            except Exception:
                                continue
                            pw = seg * pct / 100.0
                            if pw >= 1:
                                c.create_rectangle(
                                    x0, yy, x0 + pw, yy + bar_h,
                                    fill=self._color(pct), width=0)

        # 尺寸与贴边位置（保持垂直/水平方向上的原有位置，夹在屏幕内）
        if not self._placed:
            x = int(self.cfg.get("window_x", 60))
            y = int(self.cfg.get("window_y", 60))
            self._placed = True
        else:
            x, y = self.root.winfo_x(), self.root.winfo_y()
        if side == "left":
            x, y = 0, min(max(0, y), max(0, sh - hl))
        elif side == "right":
            x, y = sw - hw, min(max(0, y), max(0, sh - hl))
        elif side == "top":
            x, y = min(max(0, x), max(0, sw - hw)), 0
        else:
            x, y = min(max(0, x), max(0, sw - hw)), sh - hl
        c.configure(width=hw, height=hl)
        self.root.geometry("%dx%d+%d+%d" % (hw, hl, x, y))

    @staticmethod
    def _fmt_num(n):
        try:
            n = float(n)
            if n >= 10000:
                return "%.1fw" % (n / 10000)
            if n == int(n):
                return str(int(n))
            return "%.1f" % n
        except Exception:
            return str(n)

    def _toast(self, msg):
        self.status_text = msg
        self.redraw()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main():
    if not TK_AVAILABLE:
        print("未检测到 tkinter。请从 python.org 安装 Python（勾选 tcl/tk 组件）后重试。")
        sys.exit(1)

    enable_high_dpi()          # 必须在创建 Tk 之前
    first = acquire_single_instance_mutex()

    root = tk.Tk()
    if first is None:
        root.withdraw()
        messagebox.showinfo(APP_NAME, "额度悬浮窗已经在运行了。\n请查看屏幕上的悬浮窗。")
        return

    dpi = get_screen_dpi()
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass

    # 全局默认字体（设置窗口等原生 ttk 控件同样清晰）
    try:
        fams = set(tkfont.families(root))
        fam = "Microsoft YaHei UI" if "Microsoft YaHei UI" in fams else "TkDefaultFont"
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(family=fam, size=10)
            except Exception:
                pass
    except Exception:
        pass

    app = UsageWidget(root, dpi)  # noqa: F841
    root.mainloop()


if __name__ == "__main__":
    main()
