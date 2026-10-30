# -*- coding: utf-8 -*-
"""系统通知：Windows Shell_NotifyIcon 气泡（Win10+ 自动呈现为 Toast）
+ 阈值预警状态机。

通知弹窗部分需要调用方传入顶层窗口句柄 hwnd（int），本模块不依赖任何
UI 工具包；阈值预警状态机从 UsageWidget._check_notifications 剥离，
改为纯函数 check_notifications(snapshot, cfg, state, notify)，行为不变。"""

import sys
import threading
import time

from .burn_predict import PREDICT_WINDOWS, fmt_duration, norm_epoch, predict_calc
from .config import APP_NAME

# --------------------------------------------------------------------------
# Windows 系统通知（Shell_NotifyIcon 气泡，Win10+ 自动呈现为 Toast）
# --------------------------------------------------------------------------

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING = 0x01, 0x02

_NOTIFY_TRAY_UIN = 0x7583
_NOTIFY_ICON_ON = {"hwnd": 0, "timer": None}   # 当前托盘图标句柄与待触发的删除定时器


def _build_notify_struct(hwnd, title, text, warn):
    """构造 NOTIFYICONDATAW；非 Windows 或构造失败返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class GUID(ctypes.Structure):
            _fields_ = (("Data1", wt.DWORD), ("Data2", wt.WORD),
                        ("Data3", wt.WORD), ("Data4", wt.BYTE * 8))

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = (
                ("cbSize", wt.DWORD),
                ("hWnd", wt.HWND),
                ("uID", wt.UINT),
                ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128),
                ("dwState", wt.DWORD),
                ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64),
                ("dwInfoFlags", wt.DWORD),
                ("guidItem", GUID),
                ("hBalloonIcon", wt.HICON),
            )

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = _NOTIFY_TRAY_UIN
        nid.uFlags = NIF_ICON | NIF_TIP | NIF_INFO
        nid.hIcon = ctypes.windll.user32.LoadIconW(None, 32512)  # IDI_APPLICATION
        nid.szTip = APP_NAME[:100]
        nid.szInfo = (text or "")[:250]
        nid.szInfoTitle = (title or "")[:60]
        nid.dwInfoFlags = NIIF_WARNING if warn else NIIF_INFO
        return nid
    except Exception:
        return None


def windows_notify(hwnd, title, text, warn=False):
    """弹系统通知；托盘图标为通知临时注册，30 秒后自动移除，不常驻。"""
    if not hwnd:
        return False
    nid = _build_notify_struct(hwnd, title, text, warn)
    if nid is None:
        return False
    try:
        import ctypes
        shell = ctypes.windll.shell32
        if _NOTIFY_ICON_ON["hwnd"] == hwnd:
            shown = shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        else:
            shown = shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            if shown:
                _NOTIFY_ICON_ON["hwnd"] = hwnd
        if not shown:
            return False
        # 多次通知共用同一图标：先取消上一个待触发的删除定时器再重新计时，
        # 否则旧定时器会在新气泡展示期间删掉托盘图标，气泡被提前带没
        old = _NOTIFY_ICON_ON.get("timer")
        if old is not None:
            old.cancel()
        t = threading.Timer(30.0, _tray_icon_remove, args=(hwnd,))
        t.daemon = True
        _NOTIFY_ICON_ON["timer"] = t
        t.start()
        return True
    except Exception:
        return False


def _tray_icon_remove(hwnd):
    if _NOTIFY_ICON_ON["hwnd"] != hwnd:
        return
    _NOTIFY_ICON_ON["hwnd"] = 0
    nid = _build_notify_struct(hwnd, "", "", False)
    if nid is None:
        return
    try:
        import ctypes
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    except Exception:
        pass


# --------------------------------------------------------------------------
# 阈值预警状态机（跨阈值 / 预测耗尽 / 临近重置；每窗口期各至多一次）
# --------------------------------------------------------------------------

def new_state():
    """一个窗口的预警去重状态初值。"""
    return {"level": 0, "reset": None, "pred": False, "nigh": False}


def check_notifications(snapshot, hist, cfg, state, notify):
    """按当前用量快照逐窗口检查并触发系统通知。

    snapshot: [(供应商, 窗口名, 百分比, 重置时间戳), ...]（UI 层的快照）
    hist:     本地用量历史（烧速预测用，UI 层持有的内存快照）
    cfg:      运行配置（warn/critical 阈值、notify、burn_predict 开关）
    state:    "prov:窗口" → 去重状态 dict，本函数原地更新（调用方持有）
    notify:   回调 notify(title, text, warn=False)，由 UI 层弹系统通知

    仅在新数据到达时被调用，天然以刷新间隔节流。"""
    if not cfg.get("notify", True):
        return
    warn_at = float(cfg.get("warn_percent", 80))
    crit_at = float(cfg.get("critical_percent", 95))
    now = time.time()
    for prov, nm, pct, rst in snapshot:
        if pct is None:
            continue
        key = "%s:%s" % (prov, nm)
        st = state.setdefault(key, new_state())
        ts0 = norm_epoch(rst)
        if ts0 and st["reset"] and ts0 != st["reset"]:
            st.update(level=0, pred=False, nigh=False)   # 进入新窗口期
        if ts0:
            st["reset"] = ts0
        if pct < warn_at - 5:   # 用量明显回落（含新窗口清零）：解除预警
            st["level"] = 0
        prov_cn = "智谱" if prov == "zhipu" else "火山"
        label = ("智谱 MCP 月额度" if nm == "MCP"
                 else "%s %s窗口" % (prov_cn, nm))
        if st["level"] < 1 and pct >= warn_at:
            st["level"] = 1
            notify(label, "已用 %d%%，接近额度上限" % round(pct))
        elif st["level"] < 2 and pct >= crit_at:
            st["level"] = 2
            notify(label, "已用 %d%%，即将耗尽" % round(pct), warn=True)
        if (cfg.get("burn_predict", False)
                and nm in PREDICT_WINDOWS and not st["pred"]):
            calc = predict_calc(hist, prov, nm, pct, rst)
            if (calc and calc["reset_min"] is not None
                    and calc["exhaust_min"] < calc["reset_min"]):
                st["pred"] = True
                notify(label,
                       "按当前烧速 %s后用尽，撑不到重置（重置还需 %s）"
                       % (fmt_duration(calc["exhaust_min"]),
                          fmt_duration(calc["reset_min"])), warn=True)
        if nm == "5小时" and not st["nigh"] and ts0:
            remain = ts0 - now
            if 0 < remain <= 20 * 60 and pct >= warn_at:
                st["nigh"] = True
                notify(label, "%s后重置，坚持一下"
                       % fmt_duration(remain / 60.0))
        state[key] = st
