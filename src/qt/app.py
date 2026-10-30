# -*- coding: utf-8 -*-
"""v3 入口：DPI 策略 + 单实例保护 + 异常钩子 + QApplication 装配。

对应 v2 usage_widget.pyw 的 main()/enable_high_dpi()/_excepthook：
- DPI：Qt6 原生按显示器缩放（无需启用属性开关），这里显式声明小数
  缩放策略 PassThrough，150% 之类不再取整跳档；
- 单实例：Windows 互斥体（与 v2 同 key），其他平台用 QLockFile 兜底；
- 异常钩子：无控制台模式下把未捕获异常写入 error.log 便于排查。"""

import datetime
import os
import sys
import tempfile
import traceback

# 允许 `python src/qt/app.py` 直接运行：把 src/ 挂进 sys.path，
# 使 `core` / `qt` 两个包都能导入
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from core.config import APP_NAME, APP_VERSION, data_dir   # noqa: E402

from qt import win32                                      # noqa: E402

LOCK_NAME = "LLM_Usage_Widget_v3.lock"


def install_excepthook():
    """未捕获异常写入 error.log（无控制台打包版的唯一排查线索）。"""

    def _hook(exc_type, exc, tb):
        try:
            with open(os.path.join(data_dir(), "error.log"), "a",
                      encoding="utf-8") as f:
                f.write("\n[%s]\n" % datetime.datetime.now()
                        .strftime("%Y-%m-%d %H:%M:%S"))
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
        # 保留默认控制台输出（有控制台时仍能看到崩溃栈）
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def _setup_dpi_policy():
    """Qt6 原生缩放策略：小数缩放系数直接透传，不取整（150%/125% 保真）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass


def _already_running_message():
    """第二个实例：给一个提示框后退出（与 v2 文案一致）。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance()
    owned = False
    if app is None:
        app = QApplication(sys.argv)
        owned = True
    try:
        QMessageBox.information(
            None, APP_NAME,
            "%s已经在运行了。\n请查看屏幕上的悬浮窗。" % APP_NAME)
    except Exception:
        pass
    if owned:
        app.quit()


def main():
    _setup_dpi_policy()

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication

    # 单实例：Windows 互斥体（返回 None 表示已有实例），其他平台 QLockFile
    mutex_ok = win32.acquire_single_instance_mutex()
    if mutex_ok is None:
        _already_running_message()
        return

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    # 悬浮窗常驻：关掉设置窗/气泡等最后窗口也不能退出应用，
    # 退出只走右键/托盘菜单的「退出」（widget.quit → QApplication.quit）
    app.setQuitOnLastWindowClosed(False)

    if mutex_ok is True and sys.platform != "win32":
        lock = QLockFile(os.path.join(tempfile.gettempdir(), LOCK_NAME))
        if not lock.tryLock(0):
            _already_running_message()
            return
    else:
        lock = None

    install_excepthook()

    from qt.main_widget import UsageWidget
    from qt.tray import TrayIcon, make_icon

    widget = UsageWidget()
    tray = TrayIcon(widget)      # noqa: F841 托盘常驻引用（widget._tray 同步持有）
    widget._tray = tray
    try:
        app.setWindowIcon(make_icon(widget.t))
    except Exception:
        pass

    rc = app.exec()
    try:
        if lock is not None:
            lock.unlock()
    except Exception:
        pass
    sys.exit(rc)


if __name__ == "__main__":
    main()
