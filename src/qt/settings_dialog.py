# -*- coding: utf-8 -*-
"""设置窗口（PySide6）：左侧导航 + 右侧卡片内容 + 底部操作栏。

设计结构沿用 v2.8.7 的设置界面（五页：供应商与账号 / 刷新与预测 /
外观与主题 / 通知与预警 / 高级），_save 输出与 v2 完全相同的 patch，
配置键一个不丢。控件从 tk 手绘换成 Qt 官方 API + QSS：
- 开关 → QAbstractButton 子类手绘胶囊滑块（Switch）
- 步进器 → QSpinBox（范围夹取与 v2 的 min/max 一致）
- 分段选择器 → 一组互斥 QPushButton（pill 属性驱动 QSS）
- 卡片滚动 → QScrollArea（Qt 原生 wordWrap，窄窗不再需要手动适配换行）"""

from PySide6.QtCore import QSize, Qt, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox,
                               QDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from core.config import (APP_NAME, APP_VERSION, SCALE_SHORT, SCALE_STEPS,
                         _to_bool, _to_int, config_path, data_dir)

from .theme import C_ACCENT_V, C_ACCENT_Z, C_GREEN, C_RED, C_YELLOW

# 五个功能页（顺序即导航顺序）：key -> (标题, 副标题, 主题色点)
SET_PAGES = [
    ("providers",  "供应商与账号",
     "填写智谱 / 火山引擎的访问密钥，不用的供应商直接关闭", C_ACCENT_Z),
    ("refresh",    "刷新与预测",
     "数据拉取节奏、烧速预测与网络请求", "#45c1a5"),
    ("appearance", "外观与主题",
     "主题、缩放、透明度与贴边收起", "#a88fff"),
    ("notify",     "通知与预警",
     "阈值预警与系统通知", "#f0b34e"),
    ("advanced",   "高级",
     "开机自启、检查更新与数据文件", "#9aa3ad"),
]

# 设置窗口皮肤（色值与 v2.8.7 CTK_SKINS 完全一致）
SKINS = {
    "dark": {
        "bg": "#0f1115", "nav": "#12151a", "nav_hi": "#1a1e26",
        "nav_act": "#25314e", "card": "#171a20", "card_line": "#242833",
        "line": "#22262e", "text": "#eceff4", "dim": "#9aa3b2",
        "hint": "#6b7382", "input": "#10131a", "input_line": "#2a2f3a",
        "accent": "#6d8dff", "accent_hi": "#86a1ff",
        "on_accent": "#0b1226", "knob": "#f2f4f8", "thumb": "#2c313c",
    },
    "light": {
        "bg": "#f4f5f8", "nav": "#eceef2", "nav_hi": "#e0e3ea",
        "nav_act": "#dfe6fb", "card": "#ffffff", "card_line": "#e3e6ec",
        "line": "#e7e9ee", "text": "#1b1e24", "dim": "#555d6b",
        "hint": "#7d8492", "input": "#f9fafc", "input_line": "#d5d9e0",
        "accent": "#4059d8", "accent_hi": "#3549c0",
        "on_accent": "#ffffff", "knob": "#ffffff", "thumb": "#c9cdd6",
    },
}

_MONO_CANDIDATES = ["Consolas", "Cascadia Mono", "DejaVu Sans Mono",
                    "Courier New"]

_QSS = """
QDialog { background: %(bg)s; }
QScrollArea { background: %(bg)s; border: none; }
QLabel { background: transparent; }
QFrame#card { background: %(card)s; border: 1px solid %(card_line)s;
              border-radius: 12px; }
QFrame#navBar { background: %(nav)s; }
QFrame#footer { background: %(bg)s; }
QLineEdit, QSpinBox {
    background: %(input)s; color: %(text)s;
    border: 1px solid %(input_line)s; border-radius: 8px;
    padding: 7px 10px; min-height: 18px; }
QLineEdit:focus, QSpinBox:focus { border-color: %(accent)s; }
QSpinBox::up-button, QSpinBox::down-button {
    width: 15px; border: none; background: transparent; }
QSpinBox::up-arrow {
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid %(dim)s; width: 0; height: 0; }
QSpinBox::down-arrow {
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid %(dim)s; width: 0; height: 0; }
QPushButton {
    background: %(input)s; color: %(text)s;
    border: 1px solid %(input_line)s; border-radius: 8px;
    padding: 7px 16px; }
QPushButton:hover { background: %(nav_hi)s; }
QPushButton:pressed { background: %(input_line)s; }
QPushButton[primary="true"] {
    background: %(accent)s; color: %(on_accent)s;
    border-color: %(accent)s; font-weight: 600; }
QPushButton[primary="true"]:hover { background: %(accent_hi)s; }
QPushButton[pill="true"] {
    background: %(input)s; color: %(dim)s;
    border: 1px solid %(input_line)s; border-radius: 7px;
    padding: 5px 12px; }
QPushButton[pill="true"]:hover { color: %(text)s; }
QPushButton[pill="true"]:checked {
    background: %(accent)s; color: %(on_accent)s;
    border-color: %(accent)s; font-weight: 600; }
QPushButton[pill="true"]:checked:hover { color: %(on_accent)s; }
QCheckBox { color: %(dim)s; spacing: 7px; background: transparent; }
QCheckBox:hover { color: %(text)s; }
QCheckBox::indicator {
    width: 15px; height: 15px; border: 1px solid %(input_line)s;
    border-radius: 4px; background: %(input)s; }
QCheckBox::indicator:hover { border-color: %(accent)s; }
QCheckBox::indicator:checked {
    background: %(accent)s; border-color: %(accent)s; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical {
    background: %(thumb)s; border-radius: 4px; min-height: 40px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent; }
QToolButton { background: transparent; color: %(dim)s; border: none;
              padding: 6px 10px; border-radius: 6px; }
QToolButton:hover { background: %(nav_hi)s; color: %(text)s; }
"""


# --------------------------------------------------------------------------
# 手绘开关（替代 tk 版的 Canvas 胶囊滑块）
# --------------------------------------------------------------------------

class Switch(QAbstractButton):
    """胶囊轨道 + 圆形滑块的开关联件（setCheckable，状态即 checked）。"""

    def __init__(self, skin, parent=None):
        super().__init__(parent)
        self._skin = skin
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(42, 22)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect().height() / 2.0 - 2
        track = QColor(self._skin["accent"] if self.isChecked()
                       else self._skin["input_line"])
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(2, 2, self.width() - 4, self.height() - 4),
                          r, r)
        d = self.rect().height() - 6
        x = (self.width() - d - 5) if self.isChecked() else 5
        p.setBrush(QColor(self._skin["knob"]))
        p.drawEllipse(QRectF(x, 3, d, d))


# --------------------------------------------------------------------------
# 导航按钮（彩色圆点 + 文字，选中态高亮）
# --------------------------------------------------------------------------

class NavButton(QFrame):
    def __init__(self, skin, key, label, hue, on_pick, parent=None):
        super().__init__(parent)
        self._skin = skin
        self._key = key
        self._on_pick = on_pick
        self._hover = False
        self._active = False
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 9, 12, 9)
        lay.setSpacing(9)
        dot = QLabel(self)
        dot.setFixedSize(9, 9)
        dot.setStyleSheet("background: %s; border-radius: 4px;" % hue)
        lay.addWidget(dot)
        self._label = QLabel(label, self)
        lay.addWidget(self._label, 1)
        self._sync()

    def setActive(self, on):
        self._active = bool(on)
        self._sync()

    def _sync(self):
        s = self._skin
        if self._active:
            self.setStyleSheet("QFrame { background: %s; border-radius: 8px; }"
                               % s["nav_act"])
            self._label.setStyleSheet("color: %s;" % s["text"])
        elif self._hover:
            self.setStyleSheet("QFrame { background: %s; border-radius: 8px; }"
                               % s["nav_hi"])
            self._label.setStyleSheet("color: %s;" % s["text"])
        else:
            self.setStyleSheet("QFrame { background: transparent; }")
            self._label.setStyleSheet("color: %s;" % s["dim"])

    def enterEvent(self, ev):
        self._hover = True
        self._sync()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self._sync()
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._on_pick(self._key)
        super().mousePressEvent(ev)


# --------------------------------------------------------------------------
# 设置窗口
# --------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """设置窗口。参数与 v2 相同：

    cfg        运行配置（读初值）
    on_apply   保存回调 patch -> UI 层落盘并热生效
    on_autostart  (enable_cb, disable_cb)
    skin       "dark" / "light"（跟随悬浮窗当前生效主题）
    on_update  「检查更新」回调
    on_close   窗口关闭回调（调用方清引用）"""

    def __init__(self, parent, cfg, on_apply, on_autostart, skin="dark",
                 on_update=None, on_close=None):
        super().__init__(parent)
        self.on_apply = on_apply
        self.on_autostart = on_autostart
        self.on_update = on_update
        self.on_close = on_close
        self._closed = False
        self._skin = dict(SKINS["light" if skin == "light" else "dark"])
        self.setWindowTitle("设置 - " + APP_NAME)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setStyleSheet(_QSS % self._skin)

        self._fonts(skin)
        self._build_vars(cfg)
        self._build_ui()

        # 尺寸：舒适默认尺寸，内容超出时窗口内滚动（与 v2 同一公式）
        scr = self.screen()
        try:
            g = scr.geometry()
        except Exception:
            from PySide6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().geometry()
        w = min(940, int(g.width() * 0.94))
        h = min(720, int(g.height() * 0.9))
        self.resize(w, h)
        self.setMinimumSize(min(780, w), min(560, h))
        self.move(g.left() + max(0, (g.width() - w) // 2),
                  g.top() + max(0, (g.height() - h) // 2))

        # 原生标题栏跟随深/浅色（Win10 1809+；其他平台 no-op）
        from . import win32
        win32.apply_immersive_titlebar(self._native_hwnd(),
                                       dark=(skin != "light"))

    # ---------------- 字体 ----------------

    def _fonts(self, skin):
        try:
            families = set(QFontDatabase.families())
        except Exception:
            families = set()
        cjk = None
        for f in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                  "Noto Sans CJK SC", "WenQuanYi Micro Hei"):
            if f in families:
                cjk = f
                break
        if not cjk:
            cjk = QFont().defaultFamily()
        mono = None
        for f in _MONO_CANDIDATES:
            if f in families:
                mono = f
                break
        if not mono:
            mono = cjk
        self.f_cjk = cjk

        def mk(px, bold=False, use_mono=False):
            f = QFont(mono if use_mono else cjk)
            f.setPixelSize(px)
            f.setBold(bold)
            return f

        self.f_title = mk(20, True)
        self.f_card = mk(13, True)
        self.f_brand = mk(13, True)
        self.f_label = mk(13)
        self.f_hint = mk(12)
        self.f_nav = mk(13)
        self.f_mono = mk(12, use_mono=True)

    # ---------------- 表单值 ----------------

    def _build_vars(self, cfg):
        """把 cfg 初值读进控件（_save 再据此拼出与 v2 相同的 patch）。"""
        zcfg = cfg.get("zhipu", {})
        vcfg = cfg.get("volcano", {})
        self.z_en = bool(zcfg.get("enabled", True))
        self.z_key0 = str(zcfg.get("api_key", ""))
        self.z_base0 = str(zcfg.get("base_url", "open.bigmodel.cn"))
        self.z_30d = bool(zcfg.get("show_30d", True))
        self.z_15d = bool(zcfg.get("show_15d", True))
        self.z_7d = bool(zcfg.get("show_7d", True))
        self.v_en = bool(vcfg.get("enabled", True))
        self.v_ak0 = str(vcfg.get("access_key_id", ""))
        self.v_sk0 = str(vcfg.get("secret_access_key", ""))
        self.v_region0 = str(vcfg.get("region", "cn-beijing"))
        theme_val = str(cfg.get("theme", "auto") or "auto")
        if theme_val not in ("auto", "dark", "light"):
            theme_val = "auto"
        self.theme0 = theme_val
        try:
            scale_now = float(cfg.get("ui_scale", 1.0))
        except (TypeError, ValueError):
            scale_now = 1.0
        self.scale0 = min(SCALE_STEPS, key=lambda s: abs(s - scale_now))
        try:
            opacity = int(round(float(cfg.get("opacity", 0.96)) * 100))
        except (TypeError, ValueError):
            opacity = 96
        self.opacity0 = min(100, max(60, opacity))
        self.edge0 = bool(cfg.get("edge_dock", False))
        self.edge_glow0 = _to_bool(cfg.get("edge_glow", True), True)
        self.dock_len0 = _to_int(cfg.get("dock_len", 150), 150, 40, 240)
        self.refresh0 = _to_int(cfg.get("refresh_minutes", 5), 5, 1, 120)
        self.warn0 = _to_int(cfg.get("warn_percent", 80), 80, 10, 98)
        self.crit0 = _to_int(cfg.get("critical_percent", 95), 95, 11, 99)
        self.notify0 = bool(cfg.get("notify", True))
        self.burn0 = bool(cfg.get("burn_predict", False))

    # ---------------- UI 装配 ----------------

    def _build_ui(self):
        s = self._skin
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        # ---- 左侧导航 ----
        side = QFrame(self)
        side.setObjectName("navBar")
        side.setFixedWidth(186)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(10, 20, 10, 16)
        side_lay.setSpacing(2)
        brand = QLabel(APP_NAME, side)
        brand.setFont(self.f_brand)
        brand.setStyleSheet("color: %s;" % s["text"])
        side_lay.addWidget(brand)
        ver = QLabel("v" + APP_VERSION, side)
        ver.setFont(self.f_mono)
        ver.setStyleSheet("color: %s;" % s["hint"])
        side_lay.addWidget(ver)
        side_lay.addSpacing(16)
        self._nav = {}
        for key, label, _sub, hue in SET_PAGES:
            btn = NavButton(s, key, label, hue, self._show_page, side)
            side_lay.addWidget(btn)
            self._nav[key] = btn
        side_lay.addStretch(1)
        body.addWidget(side)

        # ---- 右侧滚动内容 ----
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pages_widget = QStackedWidget(scroll)
        scroll.setWidget(self._pages_widget)
        body.addWidget(scroll, 1)
        self._scroll = scroll
        self._pages = {}
        self._active_page = ""

        # ---- 底部操作栏 ----
        footer = QFrame(self)
        footer.setObjectName("footer")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        line = QFrame(footer)
        line.setFixedHeight(1)
        line.setStyleSheet("background: %s;" % s["line"])
        fl.addWidget(line)
        bar = QHBoxLayout()
        bar.setContentsMargins(20, 12, 30, 12)
        hint = QLabel("ESC 关闭 · 保存后立即生效", footer)
        hint.setFont(self.f_hint)
        hint.setStyleSheet("color: %s;" % s["hint"])
        bar.addWidget(hint)
        bar.addStretch(1)
        btn_cancel = QPushButton("取消", footer)
        btn_cancel.setFont(self.f_label)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self._close)
        bar.addWidget(btn_cancel)
        bar.addSpacing(10)
        btn_save = QPushButton("保存并应用", footer)
        btn_save.setProperty("primary", True)
        btn_save.setFont(self.f_label)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save)
        bar.addWidget(btn_save)
        fl.addLayout(bar)
        footer.setFixedHeight(64)
        root.addWidget(footer)

        self._show_page("providers")

    def _show_page(self, key):
        """切换功能页：页面懒构建（首次进入才创建，打开窗口更快）。"""
        if key not in self._pages:
            self._pages[key] = self._make_page(key)
            self._pages_widget.addWidget(self._pages[key])
        self._pages_widget.setCurrentWidget(self._pages[key])
        self._active_page = key
        for k, btn in self._nav.items():
            btn.setActive(k == key)
        try:
            self._scroll.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def _make_page(self, key):
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(32, 24, 36, 16)
        lay.setSpacing(0)
        self._page_head(lay, key)
        builders = {
            "providers": self._page_providers,
            "refresh": self._page_refresh,
            "appearance": self._page_appearance,
            "notify": self._page_notify,
            "advanced": self._page_advanced,
        }
        builders.get(key, self._page_providers)(lay)
        lay.addSpacing(16)
        lay.addStretch(1)
        return page

    def _page_head(self, lay, key):
        _key, label, sub, hue = next(p for p in SET_PAGES if p[0] == key)
        s = self._skin
        head = QWidget(self)
        hl = QVBoxLayout(head)
        hl.setContentsMargins(0, 2, 0, 14)
        hl.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(10)
        dot = QLabel(head)
        dot.setFixedSize(10, 10)
        dot.setStyleSheet("background: %s; border-radius: 5px;" % hue)
        row.addWidget(dot, 0, Qt.AlignVCenter)
        title = QLabel(label, head)
        title.setFont(self.f_title)
        title.setStyleSheet("color: %s;" % s["text"])
        row.addWidget(title, 0, Qt.AlignVCenter)
        row.addStretch(1)
        hl.addLayout(row)
        sub_lab = QLabel(sub, head)
        sub_lab.setFont(self.f_hint)
        sub_lab.setStyleSheet("color: %s;" % s["hint"])
        sub_lab.setWordWrap(True)
        sub_lab.setContentsMargins(20, 0, 0, 0)
        hl.addWidget(sub_lab)
        lay.addWidget(head)

    # ---------------- 卡片与行 ----------------

    def _card(self, lay, title, dot=None, switch=None, desc=None):
        """一张设置卡片：圆角面板 + 彩色圆点标题 + 右上角可选开关。"""
        s = self._skin
        card = QFrame(self)
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(20, 14, 20, 10)
        head.setSpacing(9)
        if dot:
            d = QLabel(card)
            d.setFixedSize(9, 9)
            d.setStyleSheet("background: %s; border-radius: 4px;" % dot)
            head.addWidget(d, 0, Qt.AlignVCenter)
        tlab = QLabel(title, card)
        tlab.setFont(self.f_card)
        tlab.setStyleSheet("color: %s;" % s["text"])
        head.addWidget(tlab, 0, Qt.AlignVCenter)
        head.addStretch(1)
        if switch is not None:
            head.addWidget(switch, 0, Qt.AlignVCenter)
        cl.addLayout(head)
        if desc:
            dlab = QLabel(desc, card)
            dlab.setFont(self.f_hint)
            dlab.setStyleSheet("color: %s;" % s["hint"])
            dlab.setWordWrap(True)
            dlab.setContentsMargins(20, 0, 20, 10)
            cl.addWidget(dlab)
        lay.addWidget(card)
        lay.addSpacing(14)
        return card

    def _divider(self, card):
        line = QFrame(card)
        line.setFixedHeight(1)
        line.setStyleSheet("background: %s;" % self._skin["line"])
        wrap = QHBoxLayout()
        wrap.setContentsMargins(20, 0, 20, 0)
        wrap.addWidget(line)
        card.layout().addLayout(wrap)

    def _row(self, card, title, desc=None):
        """设置行：标题(+说明)在左，返回右侧控件容器。"""
        self._divider(card)
        row = QHBoxLayout()
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(3)
        tlab = QLabel(title, card)
        tlab.setFont(self.f_label)
        tlab.setStyleSheet("color: %s;" % self._skin["text"])
        tlab.setWordWrap(True)
        left.addWidget(tlab)
        if desc:
            dlab = QLabel(desc, card)
            dlab.setFont(self.f_hint)
            dlab.setStyleSheet("color: %s;" % self._skin["hint"])
            dlab.setWordWrap(True)
            left.addWidget(dlab)
        row.addLayout(left, 1)
        right = QWidget(card)
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        row.addWidget(right, 0, Qt.AlignVCenter)
        card.layout().addLayout(row)
        return rl   # 调用方直接往右侧布局里加控件

    def _field(self, card, label, text, hint=None, secret=False):
        """表单字段行：左标签、右圆角输入框，说明挂在输入框下方。"""
        s = self._skin
        self._divider(card)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(20, 10, 20, 12)
        wrap.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(12)
        lab = QLabel(label, card)
        lab.setFont(self.f_hint)
        lab.setStyleSheet("color: %s;" % s["dim"])
        lab.setFixedWidth(110)
        row.addWidget(lab, 0, Qt.AlignVCenter)
        entry = QLineEdit(str(text), card)
        entry.setFont(self.f_mono)
        if secret:
            entry.setEchoMode(QLineEdit.Password)
            toggle = QToolButton(card)
            toggle.setText("显示")
            toggle.setFont(self.f_hint)
            toggle.setCursor(Qt.PointingHandCursor)

            def _toggle(_c=False, e=entry, b=toggle):
                if e.echoMode() == QLineEdit.Password:
                    e.setEchoMode(QLineEdit.Normal)
                    b.setText("隐藏")
                else:
                    e.setEchoMode(QLineEdit.Password)
                    b.setText("显示")

            toggle.clicked.connect(_toggle)
            row.addWidget(entry, 1)
            row.addWidget(toggle, 0, Qt.AlignVCenter)
        else:
            row.addWidget(entry, 1)
        wrap.addLayout(row)
        if hint:
            hlab = QLabel(hint, card)
            hlab.setFont(self.f_hint)
            hlab.setStyleSheet("color: %s;" % s["hint"])
            hlab.setWordWrap(True)
            hlab.setContentsMargins(122, 0, 0, 0)
            wrap.addWidget(hlab)
        card.layout().addLayout(wrap)
        return entry

    def _checks(self, card, items, title=None):
        """复选框行（智谱近 N 天用量开关）。"""
        self._divider(card)
        row = QHBoxLayout()
        row.setContentsMargins(20, 8, 20, 12)
        row.setSpacing(16)
        if title:
            tlab = QLabel(title, card)
            tlab.setFont(self.f_label)
            tlab.setStyleSheet("color: %s;" % self._skin["text"])
            row.addWidget(tlab)
            row.addSpacing(4)
        for text, cb in items:
            cb.setFont(self.f_hint)
            row.addWidget(cb)
        row.addStretch(1)
        card.layout().addLayout(row)

    def _info(self, card, title, value):
        """只读信息行：左标题，右说明。"""
        self._divider(card)
        row = QHBoxLayout()
        row.setContentsMargins(20, 9, 20, 9)
        row.setSpacing(12)
        tlab = QLabel(title, card)
        tlab.setFont(self.f_label)
        tlab.setStyleSheet("color: %s;" % self._skin["text"])
        row.addWidget(tlab)
        vlab = QLabel(str(value), card)
        vlab.setFont(self.f_hint)
        vlab.setStyleSheet("color: %s;" % self._skin["dim"])
        vlab.setWordWrap(True)
        row.addWidget(vlab, 1)
        card.layout().addLayout(row)

    def _buttons_row(self, card, specs):
        """按钮行：specs = [(文字, 回调, 是否主按钮), ...]，回调为 None 则跳过。"""
        self._divider(card)
        row = QHBoxLayout()
        row.setContentsMargins(20, 10, 20, 14)
        row.setSpacing(8)
        for text, cmd, primary in specs:
            if cmd is None:
                continue
            btn = QPushButton(text, card)
            btn.setFont(self.f_label)
            btn.setCursor(Qt.PointingHandCursor)
            if primary:
                btn.setProperty("primary", True)
            btn.clicked.connect(cmd)
            row.addWidget(btn)
        row.addStretch(1)
        card.layout().addLayout(row)

    # ---------------- 控件：步进器 / 分段选择器 ----------------

    def _stepper(self, lo, hi, step, unit="", value=0):
        sp = QSpinBox()
        sp.setFont(self.f_mono)
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setValue(int(value))
        if unit:
            sp.setSuffix(" " + unit)
        sp.setFixedWidth(110)
        sp.setAlignment(Qt.AlignCenter)
        return sp

    def _pill_select(self, options, current):
        """分段选择器：一组互斥按钮，返回 QButtonGroup（取值用 _pill_value）。"""
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, (text, value) in enumerate(options):
            btn = QPushButton(text)
            btn.setProperty("pill", True)
            btn.setCheckable(True)
            btn.setProperty("value", value)
            btn.setFont(self.f_hint)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setChecked(str(value) == str(current))
            group.addButton(btn, i)
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for btn in group.buttons():
            lay.addWidget(btn)
        return wrap, group

    @staticmethod
    def _pill_value(group):
        btn = group.checkedButton()
        if btn is None:
            return None
        v = btn.property("value")
        try:   # QVariant → Python 原生类型
            return v if isinstance(v, (str, int, float)) else str(v)
        except Exception:
            return str(v)

    # ---------------- 五个功能页（内容与 v2.8.7 一致） ----------------

    def _page_providers(self, lay):
        s = self._skin
        self.z_en_sw = Switch(s)
        self.z_en_sw.setChecked(self.z_en)
        card = self._card(lay, "智谱 GLM Coding Plan", C_ACCENT_Z,
                          switch=self.z_en_sw,
                          desc="GLM Coding Plan 套餐额度，含 5 小时 / 本周 / MCP 每月窗口")
        self.z_key_edit = self._field(card, "API Key", self.z_key0, secret=True,
                                      hint="智谱开放平台 → API Keys 页面获取")
        self.z_base_edit = self._field(card, "接口地址", self.z_base0,
                                       hint="国际版（z.ai）填 api.z.ai，一般不用改")
        self.z_30d_cb = QCheckBox("近 30 天")
        self.z_30d_cb.setChecked(self.z_30d)
        self.z_15d_cb = QCheckBox("近 15 天")
        self.z_15d_cb.setChecked(self.z_15d)
        self.z_7d_cb = QCheckBox("近 7 天")
        self.z_7d_cb.setChecked(self.z_7d)
        self._checks(card, (("近 30 天", self.z_30d_cb),
                            ("近 15 天", self.z_15d_cb),
                            ("近 7 天", self.z_7d_cb)),
                     title="详情面板历史用量")

        self.v_en_sw = Switch(s)
        self.v_en_sw.setChecked(self.v_en)
        card = self._card(lay, "火山引擎 Agent Plan", C_ACCENT_V,
                          switch=self.v_en_sw,
                          desc="账号级访问密钥（AK/SK），含 5 小时 / 本周 / 本月窗口")
        self.v_ak_edit = self._field(card, "AccessKey ID", self.v_ak0,
                                     hint="控制台 → 右上角头像 → API 访问密钥（AKLT 开头）")
        self.v_sk_edit = self._field(card, "SecretAccessKey", self.v_sk0,
                                     secret=True,
                                     hint="账号级密钥，不是方舟里的模型 API Key")
        self.v_region_edit = self._field(card, "地域", self.v_region0,
                                         hint="一般保持 cn-beijing")

        note = QLabel("密钥只保存在本机 config.json，不经过任何第三方服务器；"
                      "不需要的供应商可在卡片右上角关闭，悬浮窗即隐藏该卡片。",
                      self)
        note.setFont(self.f_hint)
        note.setStyleSheet("color: %s;" % s["hint"])
        note.setWordWrap(True)
        lay.addSpacing(2)
        lay.addWidget(note)
        lay.addSpacing(10)

    def _page_refresh(self, lay):
        s = self._skin
        card = self._card(lay, "数据刷新", "#45c1a5",
                          desc="两家供应商每次并行拉取，互不等待")
        right = self._row(card, "自动刷新间隔",
                          "手动编辑 config.json 后，右键「立即刷新」马上生效")
        self.refresh_spin = self._stepper(1, 120, 1, "分钟", self.refresh0)
        right.addWidget(self.refresh_spin)
        right.addStretch(1)

        self.burn_switch = Switch(s)
        self.burn_switch.setChecked(self.burn0)
        card = self._card(lay, "烧速预测", "#45c1a5", switch=self.burn_switch,
                          desc="按近期用量外推 5 小时 / 本周窗口何时用尽，"
                               "提前预警「撑不到重置」，默认关闭")
        self._info(card, "数据来源",
                   "本地 history.jsonl，每次刷新追加一条快照，保留 14 天")
        self._info(card, "显示条件",
                   "至少 4 个样本、跨度约 20 分钟以上；数据不足时保持安静")

        card = self._card(lay, "网络请求", "#45c1a5")
        self._info(card, "超时与重试",
                   "单次超时 20 秒；直连与系统代理两路都失败后自动重试一轮")
        self._info(card, "密钥传输",
                   "请求直接发往供应商官方 API，密钥不经过任何第三方")

    def _page_appearance(self, lay):
        card = self._card(lay, "配色主题", "#a88fff")
        right = self._row(card, "配色主题",
                          "自动模式感应悬浮窗背后的背景明暗；设置窗口跟随当前生效的主题")
        self._theme_wrap, self._theme_group = self._pill_select(
            (("自动", "auto"), ("深色", "dark"), ("浅色", "light")),
            self.theme0)
        right.addWidget(self._theme_wrap)
        right.addStretch(1)

        card = self._card(lay, "界面尺寸", "#a88fff")
        right = self._row(card, "界面缩放",
                          "觉得悬浮窗太大或太小就调这里，保存后立即生效")
        self._scale_wrap, self._scale_group = self._pill_select(
            tuple((SCALE_SHORT[s], str(s)) for s in SCALE_STEPS), self.scale0)
        right.addWidget(self._scale_wrap)
        right.addStretch(1)
        right = self._row(card, "窗口透明度", "60% ～ 100%，建议 80% 以上")
        self.opacity_spin = self._stepper(60, 100, 1, "%", self.opacity0)
        right.addWidget(self.opacity_spin)
        right.addStretch(1)

        card = self._card(lay, "贴边收起", "#a88fff")
        right = self._row(card, "贴边收起",
                          "拖到屏幕边缘自动收起为胶囊把手，悬停片刻或点击展开；"
                          "抓住把手还能拖去别的边")
        self.edge_switch = Switch(self._skin)
        self.edge_switch.setChecked(self.edge0)
        right.addWidget(self.edge_switch)
        right.addStretch(1)
        right = self._row(card, "收起把手光效",
                          "把手带亮边与呼吸光效，收起后也一眼能找到；"
                          "关闭后为静态纯色")
        self.edge_glow_switch = Switch(self._skin)
        self.edge_glow_switch.setChecked(self.edge_glow0)
        right.addWidget(self.edge_glow_switch)
        right.addStretch(1)
        right = self._row(card, "把手长度", "收起后胶囊把手的长度（40 ～ 240px）")
        self.dock_len_spin = self._stepper(40, 240, 10, "px", self.dock_len0)
        right.addWidget(self.dock_len_spin)
        right.addStretch(1)

    def _page_notify(self, lay):
        card = self._card(lay, "预警阈值", "#f0b34e",
                          desc="进度条、百分比与详情面板按当前档位变色")
        right = self._row(card, "黄色提醒阈值", "用量超过黄档时变黄")
        self.warn_spin = self._stepper(10, 98, 1, "%", self.warn0)
        right.addWidget(self.warn_spin)
        right.addStretch(1)
        right = self._row(card, "红色临界阈值",
                          "超过红档变红；保存时自动保证黄档低于红档")
        self.crit_spin = self._stepper(11, 99, 1, "%", self.crit0)
        right.addWidget(self.crit_spin)
        right.addStretch(1)
        right = self._row(card, "颜色示意")
        for color, label in ((C_GREEN, "正常"), (C_YELLOW, "偏高"),
                             (C_RED, "临界")):
            pill = QLabel(" " + label + " ")
            pill.setFont(self.f_hint)
            pill.setStyleSheet(
                "background: %s; color: #171a20; border-radius: 6px;"
                " padding: 3px 8px;" % color)
            right.addWidget(pill, 0, Qt.AlignVCenter)
        right.addStretch(1)

        self.notify_switch = Switch(self._skin)
        self.notify_switch.setChecked(self.notify0)
        card = self._card(lay, "系统通知", "#f0b34e", switch=self.notify_switch,
                          desc="跨阈值或临近重置时弹 Windows 通知，每个窗口期只提醒一次")
        self._info(card, "提醒时机",
                   "跨黄/红阈值 · 临近重置（约 20 分钟内且用量偏高）· 烧速撑不到重置")

    def _page_advanced(self, lay):
        card = self._card(lay, "开机自启", "#9aa3ad",
                          desc="登录 Windows 后自动启动悬浮窗，右键菜单里也可以设置")
        self._buttons_row(card, (("设置自启", self.on_autostart[0], False),
                                 ("取消自启", self.on_autostart[1], False)))

        card = self._card(lay, "应用", "#9aa3ad")
        self._info(card, "当前版本", "v" + APP_VERSION + "（支持应用内增量更新）")
        if self.on_update:
            self._buttons_row(card, (("检查更新", self.on_update, True),))

        card = self._card(lay, "数据文件", "#9aa3ad",
                          desc="配置与用量历史都是本机明文文件，升级、重装都不会丢失")
        self._info(card, "配置文件", config_path())
        self._buttons_row(card, (("打开配置文件夹", self._open_config_dir, False),))

    # ---------------- 交互 ----------------

    def _open_config_dir(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(data_dir()))
        except Exception:
            pass

    def _native_hwnd(self):
        """悬浮窗的置顶守护把本窗口也重新钉住时要用原生句柄。"""
        try:
            return int(self.winId())
        except Exception:
            return 0

    def reject(self):
        self._close()

    def closeEvent(self, ev):
        ev.accept()
        self._close()

    def _close(self):
        if self._closed:
            return
        self._closed = True
        cb, self.on_close = self.on_close, None
        if cb:
            try:
                cb()
            except Exception:
                pass
        self.hide()
        self.deleteLater()

    def _save(self):
        """汇总表单为配置 patch（脏值收敛规则与 v2 _save 逐条一致）。"""
        scale = self._pill_value(self._scale_group)
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 1.0
        if scale not in SCALE_STEPS:      # 脏值收敛到最近的合法档位
            scale = min(SCALE_STEPS, key=lambda s: abs(s - scale))
        theme = self._pill_value(self._theme_group) or "auto"
        if theme not in ("auto", "dark", "light"):
            theme = "auto"
        warn = int(self.warn_spin.value())
        crit = int(self.crit_spin.value())
        if warn >= crit:  # 保证黄 < 红
            warn, crit = crit - 1, crit
        patch = {
            "zhipu": {
                "enabled": bool(self.z_en_sw.isChecked()),
                "api_key": self.z_key_edit.text().strip(),
                "base_url": self.z_base_edit.text().strip() or "open.bigmodel.cn",
                "show_30d": bool(self.z_30d_cb.isChecked()),
                "show_15d": bool(self.z_15d_cb.isChecked()),
                "show_7d": bool(self.z_7d_cb.isChecked()),
            },
            "volcano": {
                "enabled": bool(self.v_en_sw.isChecked()),
                "access_key_id": self.v_ak_edit.text().strip(),
                "secret_access_key": self.v_sk_edit.text().strip(),
                "region": self.v_region_edit.text().strip() or "cn-beijing",
            },
            "theme": theme,
            "edge_dock": bool(self.edge_switch.isChecked()),
            "edge_glow": bool(self.edge_glow_switch.isChecked()),
            "dock_len": int(self.dock_len_spin.value()),
            "ui_scale": scale,
            "opacity": min(1.0, max(0.6, int(self.opacity_spin.value()) / 100.0)),
            "refresh_minutes": int(self.refresh_spin.value()),
            "warn_percent": warn,
            "critical_percent": crit,
            "notify": bool(self.notify_switch.isChecked()),
            "burn_predict": bool(self.burn_switch.isChecked()),
        }
        self._close()
        self.on_apply(patch)
