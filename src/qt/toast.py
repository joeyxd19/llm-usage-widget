# -*- coding: utf-8 -*-
"""Toast 小气泡：悬浮窗下方弹出、短暂停留后淡出。

移植 v2 UsageWidget._toast，改用 QWidget + QTimer 实现淡出。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout


class Toast(QWidget):
    """一次性气泡（每个 Toast 实例只显示一条消息）。"""

    def __init__(self, msg, theme, font_px):
        super().__init__(None)   # 独立顶层窗口，不随悬浮窗移动/销毁
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowOpacity(1.0)
        bg = theme.get("card", "#232429")
        fg = theme.get("text", "#eceef2")

        lab = QLabel(msg, self)
        lab.setStyleSheet(
            "background: %s; color: %s; padding: 7px 14px; font-size: %dpx;"
            % (bg, fg, font_px))
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(lab)

        self._fade_step = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fade)

    def popup(self, anchor_widget, delay_ms=1600):
        """在 anchor_widget（全局坐标）下方居中弹出，delay_ms 后开始淡出。"""
        self.adjustSize()
        geo = self.screen().geometry()
        x = (anchor_widget.x() + anchor_widget.width() // 2
             - self.width() // 2)
        y = anchor_widget.y() + anchor_widget.height() + 10
        x = min(max(geo.left() + 8, x), geo.right() - self.width() - 8)
        y = min(y, geo.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()
        QTimer.singleShot(delay_ms, self._start_fade)

    def _start_fade(self):
        self._fade_step = 0
        self._timer.start(28)

    def _fade(self):
        self._fade_step += 1
        alpha = 1.0 - self._fade_step * 0.12
        if alpha <= 0.05:
            self.close()
            self.deleteLater()
            return
        self.setWindowOpacity(alpha)


def show_toast(parent, msg, theme, font_px):
    """便捷入口：在 parent（悬浮窗）下方弹出一条提示气泡。"""
    try:
        t = Toast(msg, theme, font_px)
        t.popup(parent)
        return t
    except Exception:
        return None
