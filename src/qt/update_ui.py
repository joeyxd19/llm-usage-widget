# -*- coding: utf-8 -*-
"""应用内更新 UI：跨线程信号桥 + 新版本气泡 + 下载进度窗。

移植 v2 的 _show_update_bubble 与进度窗口；core.updater 的工作线程不能
直接碰 UI，全部经 UpdaterBridge 信号排队回主线程。"""

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

ACCENT_FALLBACK = "#7aa2ff"


# --------------------------------------------------------------------------
# 信号桥：updater 工作线程 → 主线程
# --------------------------------------------------------------------------

class UpdaterBridge(QObject):
    """把 core.updater 的回调转成 Qt 信号（跨线程自动排队到主线程）。"""

    prompt = Signal(object)          # info dict（tag/body/size_text/download/dismiss）
    message = Signal(str, str)       # (title, text) 提示信息
    error = Signal(str, str)         # (title, msg) 错误提示
    progressStarted = Signal(str)    # 进度窗标题（开始下载）
    progressPct = Signal(int, str)   # (百分比, 附加说明，可为空串)
    quitApp = Signal()               # 安装程序已拉起，请求退出应用

    def _on_progress(self, arg, text=None):
        """core.updater 的 on_progress 原样挂进来。

        调用形态有两种：_show_progress(str) 与 _set_progress(pct, text)。"""
        if isinstance(arg, str):
            self.progressStarted.emit(arg)
            return
        try:
            pct = int(arg)
        except (TypeError, ValueError):
            return
        self.progressPct.emit(pct, text or "")


# --------------------------------------------------------------------------
# 新版本气泡（屏幕右下角，可点击下载 / 忽略，15 秒自动消失）
# --------------------------------------------------------------------------

class _RoundedPanel(QWidget):
    """圆角卡片底板：WA_TranslucentBackground + 手绘圆角矩形。"""

    def __init__(self, radius, bg, line, parent=None):
        super().__init__(parent)
        self._radius = radius
        self._bg = bg
        self._line = line

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(self._line), 1))
        p.setBrush(QColor(self._bg))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                          self._radius, self._radius)


class UpdateBubble(QWidget):
    """发现新版本时的可点击气泡：立即更新 / 忽略，超时自动消失。

    info 为 core.updater.on_prompt 给出的 dict：
    tag / body / size_text / asset / download / dismiss。"""

    def __init__(self, info, theme, font_family, ui_scale=1.0):
        super().__init__(None)
        self._info = info
        self._acted = False          # 用户已点过按钮：自动关闭不再 dismiss
        k = max(0.75, min(1.3, ui_scale or 1.0))
        bg = theme.get("card", "#232429")
        fg = theme.get("text", "#e8eaed")
        dim = theme.get("dim", "#9aa0a6")
        line = theme.get("line", "#2e3038")
        accent = theme.get("tag_z", ACCENT_FALLBACK)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        panel = _RoundedPanel(int(12 * k), bg, line, self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(int(16 * k), int(12 * k), int(16 * k),
                               int(12 * k))
        lay.setSpacing(int(6 * k))

        f_title = QFont(font_family or QFont().defaultFamily())
        f_title.setPixelSize(max(10, int(round(11 * k))))
        f_title.setBold(True)
        f_small = QFont(font_family or QFont().defaultFamily())
        f_small.setPixelSize(max(8, int(round(9 * k))))

        title = QLabel("发现新版本 %s" % info.get("tag", ""), panel)
        title.setFont(f_title)
        title.setStyleSheet("color: %s;" % fg)
        lay.addWidget(title)

        detail = "更新包：%s（%s）\n%s" % (
            info.get("asset", ""), info.get("size_text", ""),
            (info.get("body", "") or "").strip())
        dlab = QLabel(detail.strip(), panel)
        dlab.setFont(f_small)
        dlab.setStyleSheet("color: %s;" % dim)
        dlab.setWordWrap(True)
        dlab.setFixedWidth(int(260 * k))
        lay.addWidget(dlab)

        btns = QHBoxLayout()
        btns.setSpacing(int(8 * k))
        btns.addStretch(1)
        f_btn = QFont(font_family or QFont().defaultFamily())
        f_btn.setPixelSize(max(8, int(round(9 * k))))

        btn_dismiss = QPushButton("忽略", panel)
        btn_dismiss.setFont(f_btn)
        btn_dismiss.setCursor(Qt.PointingHandCursor)
        btn_dismiss.setStyleSheet(
            "QPushButton { background: transparent; color: %s;"
            " border: 1px solid %s; border-radius: 6px; padding: 4px 12px; }"
            "QPushButton:hover { background: %s; }"
            % (fg, line, theme.get("track", "#3a3d46")))
        btn_dismiss.clicked.connect(self._do_dismiss)
        btns.addWidget(btn_dismiss)

        btn_dl = QPushButton("立即更新", panel)
        btn_dl.setFont(f_btn)
        btn_dl.setCursor(Qt.PointingHandCursor)
        btn_dl.setStyleSheet(
            "QPushButton { background: %s; color: #ffffff; border: none;"
            " border-radius: 6px; padding: 4px 14px; font-weight: 600; }"
            "QPushButton:hover { background: %s; }"
            % (accent, theme.get("tag_v", accent)))
        btn_dl.clicked.connect(self._do_download)
        btns.addWidget(btn_dl)
        lay.addLayout(btns)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.addWidget(panel)

    # ---------------- 显示与关闭 ----------------

    def show_at_bottom_right(self):
        """定位到主屏右下角（留 16px 边距），淡入后 15 秒自动消失。"""
        self.adjustSize()
        scr = self.screen()
        try:
            g = scr.geometry()
        except Exception:
            from PySide6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().geometry()
        x = g.right() - self.width() - 16
        y = g.bottom() - self.height() - 16
        self.move(max(g.left() + 8, x), max(g.top() + 8, y))
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()

        fade = QPropertyAnimationHelper.fade_in(self, 220)
        if fade is None:   # 动画不可用（如离屏平台）：直接给全不透明
            self.setWindowOpacity(1.0)

        QTimer.singleShot(15000, self._auto_close)

    def _do_download(self):
        self._acted = True
        self.close()
        try:
            self._info["download"]()
        except Exception:
            pass

    def _do_dismiss(self):
        self._acted = True
        self.close()
        try:
            self._info["dismiss"]()
        except Exception:
            pass

    def _auto_close(self):
        """超时无操作：关掉气泡并让 updater 复位 busy 状态。"""
        if not self.isVisible():
            return
        self.close()
        if not self._acted:
            try:
                self._info["dismiss"]()
            except Exception:
                pass


class QPropertyAnimationHelper:
    """极薄的淡入封装（避免本文件顶部再拉一串动画类）。"""

    @staticmethod
    def fade_in(w, ms):
        try:
            from PySide6.QtCore import QPropertyAnimation, QEasingCurve
            anim = QPropertyAnimation(w, b"windowOpacity", w)
            anim.setDuration(ms)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            return anim
        except Exception:
            return None


# --------------------------------------------------------------------------
# 下载进度窗
# --------------------------------------------------------------------------

class UpdateProgressDialog(QWidget):
    """下载进度小窗：标题 + 进度条 + 取消。

    生命周期：
    - core.updater on_progress(str) → show_for(title) 弹出；
    - on_progress(pct, text) → set_progress 更新；100% 后 2.5 秒自动关闭；
    - 下载被取消或失败（updater.busy 变 False）→ 轮询兜底自动关闭。"""

    def __init__(self, parent, updater):
        super().__init__(parent)
        self._updater = updater
        self._finished = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        panel = _RoundedPanel(10, "#232429", "#2e3038", self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)
        f = QFont()
        f.setPixelSize(12)

        self._title = QLabel("", panel)
        self._title.setFont(f)
        self._title.setStyleSheet("color: #e8eaed;")
        lay.addWidget(self._title)

        self._bar = QProgressBar(panel)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setStyleSheet(
            "QProgressBar { background: #3a3d46; border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #7aa2ff; border-radius: 3px; }")
        lay.addWidget(self._bar)

        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("取消", panel)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { background: transparent; color: #9aa0a6;"
            " border: 1px solid #2e3038; border-radius: 6px;"
            " padding: 3px 14px; }"
            "QPushButton:hover { background: #3a3d46; color: #e8eaed; }")
        btn.clicked.connect(self._cancel)
        row.addWidget(btn)
        lay.addLayout(row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.addWidget(panel)

        # 下载失败 / 取消后 updater.busy 复位为 False：轮询兜底关窗，
        # 不能让进度窗在错误路径上永远挂着
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._poll_tick)

    # ---- 槽 ----

    def show_for(self, title):
        """开始下载：弹出并居中于悬浮窗所在屏幕。"""
        if self.isVisible():
            return
        self._finished = False
        self._title.setText(title or "正在下载…")
        self._bar.setValue(0)
        self.adjustSize()
        try:
            g = self.screen().geometry()
        except Exception:
            from PySide6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().geometry()
        x = g.center().x() - self.width() // 2
        y = g.center().y() - self.height() // 2
        self.move(x, y)
        self.show()
        self.raise_()
        self._poll.start()

    def set_progress(self, pct, text=""):
        if not self.isVisible():
            return
        if text:
            self._title.setText(text)
        try:
            self._bar.setValue(max(0, min(100, int(pct))))
        except Exception:
            return
        if int(pct) >= 100 and not self._finished:
            self._finished = True
            self._title.setText("下载完成，准备安装…")
            # 0.8 秒后 core.updater 会请求退出应用；窗口最多再留 2.5 秒
            QTimer.singleShot(2500, self._hide_now)

    # ---- 内部 ----

    def _cancel(self):
        try:
            self._updater.cancel()
        except Exception:
            pass
        self._title.setText("正在取消…")
        self._hide_soon()

    def _poll_tick(self):
        if not self.isVisible() or self._finished:
            if not self.isVisible():
                self._poll.stop()
            return
        try:
            busy = bool(self._updater.busy)
        except Exception:
            busy = True
        if not busy:
            self._hide_now()

    def _hide_soon(self):
        QTimer.singleShot(1200, self._hide_now)

    def _hide_now(self):
        self._poll.stop()
        self.hide()
