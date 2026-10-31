# -*- coding: utf-8 -*-
"""系统托盘（v3 新增能力）。

v2 只用 Shell_NotifyIcon 弹通知气泡，无常驻托盘；v3 用 QSystemTrayIcon
提供常驻入口：左键弹出/收起悬浮窗，右键菜单覆盖高频操作（立即刷新、
设置、检查更新、开机自启、退出）。图标程序化绘制（仓库无图标资源），
跟随悬浮窗主题与各窗口用量百分比。"""

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.config import APP_NAME

from . import detail_panel as dp


def make_icon(theme, pct_z=None, pct_v=None, px=64):
    """绘制托盘图标：圆角卡片底 + 智谱/火山两条微型进度条。"""
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    card = QColor(theme.get("card", "#232429"))
    line = QColor(theme.get("line", "#2e3038"))
    p.setPen(QPen(line, px / 24.0))
    p.setBrush(card)
    p.drawRoundedRect(QRectF(px * 0.06, px * 0.06, px * 0.88, px * 0.88),
                      px * 0.22, px * 0.22)

    def bar(y, pct):
        # 一条进度条：底槽 + 按预警阈值取色的填充段
        x0, w, h = px * 0.2, px * 0.6, px * 0.11
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.get("track", "#3a3d46")))
        p.drawRoundedRect(QRectF(x0, y, w, h), h / 2.0, h / 2.0)
        if pct is None:
            return
        try:
            pct = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            return
        fake_theme = {"dim": theme.get("dim", "#9aa0a6"),
                      "ok": theme.get("ok", "#4ade80"),
                      "warn": theme.get("warn", "#fbbf24"),
                      "crit": theme.get("crit", "#f87171")}
        fake_cfg = {"critical_percent": 95, "warn_percent": 80}
        p.setBrush(QColor(dp.color_for(fake_theme, fake_cfg, pct)))
        p.drawRoundedRect(QRectF(x0, y, max(h, w * pct / 100.0), h),
                          h / 2.0, h / 2.0)

    bar(px * 0.36, pct_z)
    bar(px * 0.55, pct_v)
    p.end()
    return QIcon(pm)


class TrayIcon(QSystemTrayIcon):
    """常驻托盘。widget 为 UsageWidget 实例（菜单/图标状态都从它读）。"""

    def __init__(self, widget):
        self.widget = widget
        super().__init__(make_icon(widget.t), widget)
        self.setToolTip(APP_NAME)
        self._theme_eff = widget.theme_eff
        self._menu = None
        self._act_refresh = None
        self._build_menu()
        self.activated.connect(self._on_activated)
        # 定时同步悬浮窗状态（用量百分比 → 图标进度条与悬停提示）
        self._tick = QTimer(self)
        self._tick.setInterval(5000)
        self._tick.timeout.connect(self._sync_status)
        self._tick.start()
        self._sync_status()

    # ---------------- 菜单 ----------------

    def _build_menu(self):
        try:
            if self._menu is not None:
                self._menu.close()
                self._menu.deleteLater()
        except Exception:
            pass
        w = self.widget
        t = w.t
        menu = QMenu()
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowStaysOnTopHint)
        # 菜单配色跟随悬浮窗主题（与悬浮窗右键菜单同一套 QSS）
        menu.setStyleSheet(
            "QMenu { background: %(card)s; color: %(text)s;"
            " border: 1px solid %(line)s; border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 5px 22px 5px 12px; border-radius: 5px; }"
            "QMenu::item:selected { background: %(hover)s; }"
            "QMenu::separator { height: 1px; background: %(line)s;"
            " margin: 4px 8px; }"
            % {"card": t["card"], "text": t["text"], "line": t["line"],
               "hover": t["tag_z"]})
        self._menu = menu

        self._act_refresh = menu.addAction("立即刷新", w.refresh_async)
        menu.addAction("显示悬浮窗", self._show_widget)
        menu.addAction("设置…", w.open_settings)
        if w.updater and w.updater.pkg:
            menu.addAction("检查更新…", w._manual_update_check)
        menu.addSeparator()

        theme_menu = menu.addMenu("配色主题")
        group = QActionGroup(theme_menu)
        group.setExclusive(True)
        for mode, label in (("auto", "自动（跟随背景）"), ("dark", "深色"),
                            ("light", "浅色")):
            a = theme_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(str(w.cfg.get("theme", "auto") or "auto") == mode)
            a.triggered.connect(lambda _c=False, m=mode: w._set_theme(m))
            group.addAction(a)

        auto_menu = menu.addMenu("开机自启")
        auto_menu.addAction("设置开机自启", w.enable_autostart)
        auto_menu.addAction("取消开机自启", w.disable_autostart)

        menu.addSeparator()
        menu.addAction("退出", w.quit)
        self.setContextMenu(menu)

    # ---------------- 行为 ----------------

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_widget()

    def _show_widget(self):
        """左键 / 菜单「显示悬浮窗」一键找回：取消贴边收起、位置丢失时
        拉回屏幕可见区，再前置激活——从托盘一定能把悬浮窗找回来。"""
        self.widget.restore_visible()

    # ---------------- 状态同步 ----------------

    def _prov_pct(self, prov):
        """该供应商首个窗口的百分比与「名称 xx%」提示串。"""
        d = self.widget.data.get(prov)
        if not d or not d.get("ok"):
            return None, ""
        if prov == "zhipu":
            pairs = [("5小时", dp.safe_pct(d.get("five_hour"))),
                     ("本周", dp.safe_pct(d.get("weekly")))]
        else:
            pairs = [(str(nm), dp.safe_ratio_pct(u, q))
                     for nm, u, q, _r in (d.get("windows") or [])[:2]]
        pairs = [(nm, p) for nm, p in pairs if p is not None]
        if not pairs:
            return None, ""
        return pairs[0][1], " · ".join("%s %d%%" % (nm, round(p))
                                       for nm, p in pairs)

    def _sync_status(self):
        """用量 → 图标进度条与悬停提示；主题变了也顺带重建菜单配色。"""
        w = self.widget
        if self._theme_eff != w.theme_eff:
            self._theme_eff = w.theme_eff
            try:
                self._build_menu()
            except Exception:
                pass

        pz, txt_z = self._prov_pct("zhipu")
        pv, txt_v = self._prov_pct("volcano")
        self.setIcon(make_icon(w.t, pz, pv))

        tip = APP_NAME
        if txt_z:
            tip += "\n智谱 " + txt_z
        elif w.errors.get("zhipu"):
            tip += "\n智谱：" + w.errors["zhipu"][:30]
        if txt_v:
            tip += "\n火山 " + txt_v
        elif w.errors.get("volcano"):
            tip += "\n火山：" + w.errors["volcano"][:30]
        if w.last_ok_time:
            tip += "\n更新于 " + w.last_ok_time.strftime("%H:%M:%S")
        self.setToolTip(tip)
        try:
            if self._act_refresh is not None:
                self._act_refresh.setEnabled(not w.updating)
        except Exception:
            pass
