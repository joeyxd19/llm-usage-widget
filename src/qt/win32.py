# -*- coding: utf-8 -*-
"""Win32 平台助手（ctypes 调用，非 UI 依赖）：置顶守护、背景亮度采样、
单实例互斥体。全部函数在非 Windows 平台安全降级（no-op / None / True）。"""

import sys


def _user32():
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return ctypes.windll.user32
    except Exception:
        return None


# Windows SetWindowPos 常量
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010


def _set_topmost(hwnd, insert_after):
    if not hwnd or sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = _user32()
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_bool
        user32.SetWindowPos(
            ctypes.c_void_p(hwnd), ctypes.c_void_p(insert_after),
            0, 0, 0, 0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
    except Exception:
        pass


def assert_topmost(hwnd):
    """用 SetWindowPos 把窗口重新钉到最顶层（HWND_TOPMOST），不激活也不改位置尺寸。

    Qt 的 WindowStaysOnTopHint 与 tk 的 -topmost 一样会被系统在若干时刻
    悄悄清掉（切换全屏应用、切换虚拟桌面等），需要定时重新断言。
    SWP_NOACTIVATE 保证重新置顶时不抢走当前焦点窗口的输入。"""
    _set_topmost(hwnd, _HWND_TOPMOST)


def release_topmost(hwnd):
    """把窗口从置顶层摘下（HWND_NOTOPMOST）。菜单显示期间暂时让出置顶用。"""
    _set_topmost(hwnd, _HWND_NOTOPMOST)


def sample_background_luminance(x, y, w, h, screen_w, screen_h):
    """采样矩形四周（外扩 8px）背景亮度的中位数（0~255）。
    只取窗口外的点，不会被悬浮窗自己污染；失败返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = _user32()
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
    """单实例保护（Windows 互斥体，与 v2 同名同 key）。

    返回 True 表示获得；返回 None 表示已有实例在运行；
    非 Windows 平台不做互斥（与 v2 一致，跨平台场景由 app.py 的
    QLockFile 兜底）。"""
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


def apply_immersive_titlebar(hwnd, dark=True):
    """让原生标题栏跟随深/浅色（Win10 1809+ DWMWA_USE_IMMERSIVE_DARK_MODE）。

    v2 SettingsDialog 的同款处理：深色界面配白色标题栏会很突兀。
    属性 ID 在不同 Win10 版本分别是 19 / 20，逐个尝试。"""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), 4) == 0:
                break
    except Exception:
        pass
