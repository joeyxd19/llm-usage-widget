# -*- coding: utf-8 -*-
"""悬浮窗主窗口（PySide6）。

从 v2 UsageWidget 移植：
- 无边框 + 常驻置顶（FramelessWindowHint + WindowStaysOnTopHint + 置顶守护）
- WA_TranslucentBackground 实现真圆角（替代 v2 的透明色 chroma key）
- 贴边收起/悬停展开：QPropertyAnimation（位移 + 透明度并行）
- 拖动、边缘检测、位置记忆与越界复位、深浅主题（自动=背景亮度采样）
- 刷新链：工作线程并行拉取两家供应商，信号回主线程应用
- 阈值/烧速系统通知（core.notify）、本地历史（core.burn_predict）

尺寸公式与 v2 一致，坐标一律用 Qt 逻辑像素（DPI 原生缩放，
用户缩放 ui_scale 直接作为像素系数 k，不再乘 DPI/96）。"""

import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup
from PySide6.QtGui import QCursor, QFont, QFontDatabase, QPainter, QActionGroup
from PySide6.QtWidgets import QMenu, QWidget

from core import notify as core_notify
from core.burn_predict import (HIST_KEY_MAP, append_history, load_history,
                               norm_epoch)
from core.config import APP_NAME, APP_VERSION, _to_int, deep_merge, is_placeholder, \
    load_config, save_config_patch

from . import detail_panel as dp
from . import win32
from .theme import FONT_FAMILY_CANDIDATES, THEMES


def _f(v, default):
    """宽松转 float（脏配置容错）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class _RefreshBridge(QObject):
    """工作线程 → 主线程的结果桥（跨线程信号自动排队）。"""

    done = Signal(object, object, object)   # zhipu 结果, volcano 结果, 配置快照


class UsageWidget(QWidget):

    def __init__(self):
        super().__init__(None)
        self.cfg = load_config()
        self.data = {"zhipu": None, "volcano": None}
        self.errors = {"zhipu": "", "volcano": ""}
        self.last_ok_time = None
        self.updating = False
        self.status_text = ""
        self._placed = False
        self._drag_offset = None
        self._moved = False
        # ---- 历史与通知 ----
        self._hist = (load_history(8 * 86400)
                      if self.cfg.get("burn_predict", False) else [])
        self._notify_state = {}                # "prov:窗口" → 预警去重状态
        self._hwnd_cache = 0

        # ---- 贴边收起状态 ----
        self.dock_state = "none"   # none / docked / expanded / expanding
        self.dock_side = str(self.cfg.get("dock_side", "none") or "none")
        self._animating = False
        self._anim_group = None
        self._suppress_collapse = False
        # ---- 详情面板倒计时刷新 ----
        self._detail_tick = None
        # ---- 置顶守护 ----
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._topmost_tick)
        self._topmost_paused = False   # 右键菜单显示期间暂停置顶
        self._topmost_hwnd = 0
        # ---- 定时器 ----
        self._collapse_timer = QTimer(self)     # 展开态 → 自动收回
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse_to_dock)
        self._recheck_timer = QTimer(self)      # Leave 误报复核
        self._recheck_timer.setSingleShot(True)
        self._recheck_timer.timeout.connect(self._recheck_pointer)
        self._drag_timer = QTimer(self)         # 拖动节流（约 10ms 应用一次）
        self._drag_timer.setSingleShot(True)
        self._drag_timer.timeout.connect(self._apply_drag_move)
        self._detail_timer = QTimer(self)       # 面板停留期间倒计时刷新
        self._detail_timer.setSingleShot(True)
        self._detail_timer.timeout.connect(self._detail_tick_cb)
        self._refresh_timer = QTimer(self)      # 自动刷新链
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh_async)
        # ---- 配色主题 ----
        self.theme_eff = "dark"
        self.t = dict(THEMES["dark"])
        self.cur_w = 0
        self.cur_h = 0
        self._settings_open = None

        # 启动时恢复上次的贴边收起状态
        if (self.cfg.get("edge_dock", True)
                and self.dock_side in ("left", "right", "top", "bottom")):
            self.dock_state = "docked"

        self._metrics()
        self._pick_fonts()
        self._bridge = _RefreshBridge()
        self._bridge.done.connect(self._apply_refresh)
        self._setup_updater()
        self._build_window()
        self._build_menu()
        self._apply_theme(force=True)
        self.redraw()
        if self._needs_setup():
            self.status_text = "未配置，右键打开设置"
            self.redraw()
            QTimer.singleShot(500, self.open_settings)
        QTimer.singleShot(300, self.refresh_async)
        QTimer.singleShot(30000, self._silent_update_check)
        self._start_topmost_guard()

    # ---------------- 尺寸与字体 ----------------

    def _metrics(self):
        us = _f(self.cfg.get("ui_scale", 1.0), 1.0)
        us = min(1.3, max(0.75, us))
        self.uscale = us
        # Qt 逻辑像素已按系统 DPI 原生缩放，这里只乘用户缩放
        self.k = us
        self.bar_h = max(4, int(5 * self.k))
        self.radius = max(7, int(10 * self.k))

    def _pick_fonts(self):
        try:
            families = set(QFontDatabase.families())
        except Exception:
            families = set()
        fam = None
        for f in FONT_FAMILY_CANDIDATES:
            if f in families:
                fam = f
                break
        if not fam:
            fam = QFont().defaultFamily()
        self.font_family = fam
        us = self.uscale

        def mk(size, bold=False):
            f = QFont(fam)
            f.setPixelSize(max(6, int(round(size * us))))
            f.setBold(bold)
            return f

        self.fonts = {
            "title": mk(9, True),
            "text": mk(9),
            "small": mk(7),
            "pct": mk(11, True),
        }

    # ---------------- 窗口 ----------------

    def _build_window(self):
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # 真圆角
        self._restore_alpha()
        self.setMouseTracking(False)

    def _screen(self):
        try:
            scr = self.screen()
            if scr is not None:
                return scr
        except Exception:
            pass
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.primaryScreen()

    def _screen_size(self):
        g = self._screen().geometry()
        return g.width(), g.height()

    def _restore_alpha(self):
        self.setWindowOpacity(min(1.0, max(0.3, _f(self.cfg.get("opacity", 0.96), 0.96))))

    def redraw(self):
        """总入口：贴边收起时画把手，其余状态都是完整详情面板。"""
        if self.dock_state == "docked":
            self._layout_handle_window()
        else:
            self._layout_detail_window()
        self.update()

    def _detail_layout(self):
        return dp.layout_blocks(self.data, self.errors, self.cfg,
                                self._hist, self.k)

    def _compute_layout_size(self):
        return self._detail_layout()[:2]

    def _status_right(self):
        if self.updating:
            return "刷新中…"
        if self.last_ok_time:
            return "更新于 " + self.last_ok_time.strftime("%H:%M:%S")
        return "尚未更新"

    def _layout_detail_window(self):
        w, h, blocks, pad = self._detail_layout()
        self.cur_w, self.cur_h = w, h
        self.resize(w, h)
        if not self._placed:
            x, y0 = self._clamp_start_pos()
            self.move(x, y0)
            self._placed = True

    def paintEvent(self, _ev):
        p = QPainter(self)
        if self.dock_state == "docked":
            hw, hl = self._handle_size()
            vertical = self.dock_side in ("left", "right")
            dp.paint_handle(p, hw, hl, self.t,
                            dp.progress_groups(self.data, self.cfg),
                            self.k, self.cfg, vertical)
        else:
            w, h, blocks, pad = self._detail_layout()
            dp.paint_detail(p, w, h, blocks, pad, self.t, self.cfg,
                            self.fonts, self.radius, self._status_right(),
                            self.updating, self.last_ok_time, self.k,
                            APP_VERSION, self.font_family)

    # ---------------- 右键菜单 ----------------

    def _build_menu(self):
        try:
            self.menu.close()
            self.menu.deleteLater()
        except Exception:
            pass
        t = self.t
        self.menu = QMenu(self)
        # 悬浮窗常驻置顶：菜单弹窗也带上置顶标记，避免被压在下面
        self.menu.setWindowFlags(self.menu.windowFlags() | Qt.WindowStaysOnTopHint)
        # 菜单配色跟随悬浮窗主题（QSS 作用于菜单及其子菜单）
        self.menu.setStyleSheet(
            "QMenu { background: %(card)s; color: %(text)s;"
            " border: 1px solid %(line)s; border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 5px 22px 5px 12px; border-radius: 5px; }"
            "QMenu::item:selected { background: %(hover)s; }"
            "QMenu::separator { height: 1px; background: %(line)s;"
            " margin: 4px 8px; }"
            % {"card": t["card"], "text": t["text"], "line": t["line"],
               "hover": t["tag_z"]})

        act = self.menu.addAction("立即刷新", self.refresh_async)
        act.setEnabled(not self.updating)
        self.menu.addAction("设置…", self.open_settings)
        if self.updater and self.updater.pkg:
            self.menu.addAction("检查更新…", self._manual_update_check)
        self.menu.addSeparator()

        # 配色主题（互斥三选）
        theme_menu = self.menu.addMenu("配色主题")
        group = QActionGroup(theme_menu)
        group.setExclusive(True)
        self._theme_actions = {}
        for mode, label in (("auto", "自动（跟随背景）"), ("dark", "深色"),
                            ("light", "浅色")):
            a = theme_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(str(self.cfg.get("theme", "auto") or "auto") == mode)
            a.triggered.connect(lambda _c=False, m=mode: self._set_theme(m))
            group.addAction(a)
            self._theme_actions[mode] = a

        dock_act = self.menu.addAction("贴边自动收起", lambda: None)
        dock_act.setCheckable(True)
        dock_act.setChecked(bool(self.cfg.get("edge_dock", True)))
        dock_act.triggered.connect(
            lambda checked: self._toggle_edge_dock(checked))
        self._dock_action = dock_act
        self.menu.addSeparator()

        auto_menu = self.menu.addMenu("开机自启")
        auto_menu.addAction("设置开机自启", self.enable_autostart)
        auto_menu.addAction("取消开机自启", self.disable_autostart)

        self.menu.addSeparator()
        self.menu.addAction("退出", self.quit)

    def _popup_menu(self, global_pos):
        self._topmost_paused = True
        if not self._topmost_hwnd:
            self._topmost_hwnd = self._native_hwnd()
        win32.release_topmost(self._topmost_hwnd)
        try:
            self.menu.exec(global_pos)
        finally:
            self._topmost_paused = False
            win32.assert_topmost(self._topmost_hwnd)
            self._suppress_collapse = False
            # 菜单关闭后，若指针已不在窗口上则启动收回计时
            if (self.dock_state == "expanded" and not self._animating
                    and not self._pointer_in_window(QCursor.pos())):
                self._collapse_timer.start(300)

    def _native_hwnd(self):
        try:
            return int(self.winId())
        except Exception:
            return 0

    def contextMenuEvent(self, _ev):
        self._cancel_collapse_timer()
        # 右键菜单打开期间不要自动收回（指针移到菜单上会触发 Leave）
        self._suppress_collapse = True
        self._popup_menu(QCursor.pos())

    def _toggle_edge_dock(self, checked):
        """开/关贴边自动收起。关闭时若正处于收起状态则恢复普通窗口。"""
        val = bool(checked)
        self.cfg["edge_dock"] = val
        save_config_patch({"edge_dock": val})
        if not val and self.dock_state == "docked":
            self._snap_to_expanded()  # 直接恢复普通窗口（不会再自动收回）

    def _set_theme(self, mode):
        """右键菜单切换配色主题，立即生效并保存。"""
        if mode not in ("auto", "dark", "light"):   # 脏值收敛
            mode = "auto"
        self.cfg["theme"] = mode
        save_config_patch({"theme": mode})
        for m, act in getattr(self, "_theme_actions", {}).items():
            act.setChecked(m == mode)
        self._apply_theme(force=True)
        self.redraw()

    # ---------------- 拖动 ----------------

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._cancel_collapse_timer()
        self._cancel_detail_tick()
        # 从收起把手态开始拖动：先瞬间恢复成正常窗口（贴边展开位）。
        # 展开态不在此转自由窗口 —— 纯点击（未移动）时必须保持
        # dock_state == "expanded"，否则 Leave 后不会自动收回（卡住）
        snap_pos = None
        if self.dock_state == "docked":
            snap_pos = self._snap_to_expanded()
        gp = e.globalPosition().toPoint()
        if snap_pos is not None:
            wx, wy = snap_pos
        else:
            wx, wy = self.x(), self.y()
        self._drag_offset = (gp.x() - wx, gp.y() - wy)
        self._drag_origin = (wx, wy)
        self._moved = False
        self._drag_pending = None

    def mouseMoveEvent(self, e):
        if not self._drag_offset or not (e.buttons() & Qt.LeftButton):
            return
        gp = e.globalPosition().toPoint()
        x = gp.x() - self._drag_offset[0]
        y = gp.y() - self._drag_offset[1]
        ox, oy = self._drag_origin
        if abs(x - ox) + abs(y - oy) > 3:
            self._moved = True
        # 节流：合并高频鼠标事件，约每 10ms 应用一次
        self._drag_pending = (x, y)
        if not self._drag_timer.isActive():
            self._drag_timer.start(10)

    def _apply_drag_move(self):
        if self._drag_pending and self._drag_offset:
            x, y = self._drag_pending
            self._drag_pending = None
            self.move(x, y)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        frm = None
        if self._drag_pending:
            frm = self._drag_pending
            self._drag_pending = None
            self.move(*frm)
        self._drag_offset = None
        self._drag_timer.stop()
        if not self._moved:
            return
        # 拖动结束时检测是否贴近屏幕边缘 → 自动收起
        side = self._detect_edge() if self.cfg.get("edge_dock", True) else None
        if side:
            self._dock_to(side, frm=frm)
            return
        if self.dock_state == "expanded":
            # 从贴边展开态被拖走：转为自由窗口，停用自动收回
            self.dock_state = "none"
            self._placed = True
            self._cancel_detail_tick()
        if self.dock_side != "none":
            self.dock_side = "none"
            save_config_patch({"dock_side": "none"})
        self._save_window_pos()
        if self._apply_theme():   # 拖到了明暗不同的背景上，自动换主题
            self.redraw()

    def _save_window_pos(self):
        save_config_patch({"window_x": self.x(), "window_y": self.y()})

    def _detect_edge(self, threshold=26):
        """返回当前窗口离哪条屏幕边最近（在阈值内），否则 None。"""
        x, y = self.x(), self.y()
        w, h = self.width(), self.height()
        sw, sh = self._screen_size()
        dists = {"left": x, "top": y,
                 "right": sw - (x + w), "bottom": sh - (y + h)}
        side = min(dists, key=dists.get)
        return side if dists[side] <= threshold else None

    # ---------------- 配色主题 ----------------

    def _theme_sample_rect(self):
        """返回用于背景取样的矩形 (x, y, w, h)。"""
        w, h = self._compute_layout_size()
        if self._placed:
            x, y = self.x(), self.y()
        else:
            x = int(_to_int(self.cfg.get("window_x", 120), 120))
            y = int(_to_int(self.cfg.get("window_y", 120), 120))
        # 贴边收起时窗口大半在屏幕外，改用贴边展开位取样
        if self.dock_state == "docked":
            sw, sh = self._screen_size()
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
        if mode not in ("auto", "dark", "light"):   # 脏配置收敛
            mode = "auto"
        eff = mode
        if mode == "auto":
            try:
                x, y, w, h = self._theme_sample_rect()
                sw, sh = self._screen_size()
                lum = win32.sample_background_luminance(x, y, w, h, sw, sh)
            except Exception:
                lum = None
            eff = "light" if (lum is not None and lum >= 150) else "dark"
        changed = (eff != self.theme_eff) or force
        self.theme_eff = eff
        self.t = THEMES[eff]
        return changed

    # ---------------- 贴边自动收起 ----------------

    def _cancel_collapse_timer(self):
        self._collapse_timer.stop()
        self._recheck_timer.stop()

    def _cancel_detail_tick(self):
        self._detail_timer.stop()

    def _animate_move(self, tx, ty, done=None, frm=None, fade=True):
        """QPropertyAnimation 缓出移动到 (tx, ty)，结束回调 done。
        frm 可显式指定起点；fade=True 时并行做透明度淡入淡出。"""
        if self._anim_group is not None:
            self._anim_group.stop()
            self._anim_group.deleteLater()
            self._anim_group = None
            self._animating = False
            self._restore_alpha()   # 上一次被打断：先把透明度恢复到位
        if frm is not None:
            sx, sy = frm
        else:
            sx, sy = self.x(), self.y()
        dx, dy = tx - sx, ty - sy
        if abs(dx) < 1 and abs(dy) < 1:
            if done:
                done()
            return
        dist = max(abs(dx), abs(dy))
        # 时长随距离自适应（与 v2 帧数公式一致：每 18px 约 9ms，54~144ms）
        duration = int(max(6, min(16, int(round(dist / 18.0)))) * 9)
        target_alpha = min(1.0, max(0.3, _f(self.cfg.get("opacity", 0.96), 0.96)))

        group = QParallelAnimationGroup(self)
        pos_anim = QPropertyAnimation(self, b"pos")
        pos_anim.setDuration(duration)
        pos_anim.setStartValue(QPoint(sx, sy))
        pos_anim.setEndValue(QPoint(tx, ty))
        pos_anim.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(pos_anim)
        if fade:
            op_anim = QPropertyAnimation(self, b"windowOpacity")
            op_anim.setDuration(duration)
            op_anim.setStartValue(max(0.0, target_alpha * 0.35))
            op_anim.setEndValue(target_alpha)
            group.addAnimation(op_anim)
        self._anim_group = group
        self._animating = True

        def _finished():
            self._anim_group = None
            self._animating = False
            group.deleteLater()
            self._restore_alpha()
            if done:
                done()

        group.finished.connect(_finished)
        group.start()

    def _snap_to_expanded(self):
        """从贴边状态瞬间恢复为正常窗口（贴边展开位），返回新位置 (x, y)。"""
        self._cancel_collapse_timer()
        self._cancel_detail_tick()
        side = self.dock_side
        w, h = self._compute_layout_size()
        sw, sh = self._screen_size()
        x, y = self.x(), self.y()
        # 与悬停展开同一位置规则：沿把手中心恢复为面板
        cx = x + self.width() // 2
        cy = y + self.height() // 2
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
        self.resize(w, h)
        self.move(x, y)
        self.redraw()
        return x, y

    def _handle_size(self):
        """贴边把手的 (宽, 高)，把手绘制与入坞/收回定位共用。"""
        k = self.k
        side = self.dock_side
        sw, sh = self._screen_size()
        t = max(7, int(12 * k))      # 露出厚度
        # 把手长度：设置「长条长度」可调（逻辑像素，随缩放走），默认 150，限 40~240
        dock_len = _f(self.cfg.get("dock_len", 150), 150.0)
        grip = int(min(240.0, max(40.0, dock_len)) * k)
        if side in ("left", "right"):
            return t, max(t * 4, min(grip, sh - 4))
        return max(t * 4, min(grip, sw - 4)), t

    def _layout_handle_window(self):
        """把手态窗口尺寸与贴边位置（保持原位，夹在屏幕内）。"""
        hw, hl = self._handle_size()
        side = self.dock_side
        sw, sh = self._screen_size()
        if not self._placed:
            x = int(_to_int(self.cfg.get("window_x", 60), 60))
            y = int(_to_int(self.cfg.get("window_y", 60), 60))
            self._placed = True
        else:
            x, y = self.x(), self.y()
        if side == "left":
            x, y = 0, min(max(0, y), max(0, sh - hl))
        elif side == "right":
            x, y = sw - hw, min(max(0, y), max(0, sh - hl))
        elif side == "top":
            x, y = min(max(0, x), max(0, sw - hw)), 0
        else:
            x, y = min(max(0, x), max(0, sw - hw)), sh - hl
        self.resize(hw, hl)
        self.move(x, y)

    def _dock_to(self, side, frm=None):
        """滑入收起到指定边，之后由把手持续显示微型进度条。"""
        self._cancel_collapse_timer()
        self.dock_side = side
        self._animating = True
        save_config_patch({"dock_side": side})
        w, h = self.cur_w, self.cur_h
        if frm is None:
            frm = (self.x(), self.y())
        x, y = frm
        sw, sh = self._screen_size()
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
            self.dock_state = "docked"
            # 窗口已在屏幕外（仅剩 1px 透明边），先把把手方向坐标调到原位
            # （发生在屏幕外看不见），再重绘成把手
            if side == "left":
                self._handle_home = (0, hy)
                self.move(-w + 1, hy)
            elif side == "right":
                self._handle_home = (sw - hw, hy)
                self.move(sw - 1, hy)
            elif side == "top":
                self._handle_home = (hx, 0)
                self.move(hx, -h + 1)
            else:
                self._handle_home = (hx, sh - hl)
                self.move(hx, sh - 1)
            self._apply_theme()
            self.redraw()          # 重绘为把手（含微型进度条）
            self._save_window_pos()

        self._animate_move(tx, ty, done, frm=frm, fade=True)

    def _expand_from_dock(self):
        """从收起状态滑出展开（鼠标移上把手时触发），显示完整详情面板。"""
        if self._animating or self.dock_state != "docked":
            return
        self._cancel_collapse_timer()
        side = self.dock_side
        w, h = self._compute_layout_size()   # 详情面板尺寸
        sw, sh = self._screen_size()
        x, y = self.x(), self.y()
        # 面板沿把手中心展开（左右贴边垂直居中、上下贴边水平居中），
        # 指针始终落在展开后的面板内，不会触发误收回
        cx = x + self.width() // 2
        cy = y + self.height() // 2
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
        self.resize(w, h)
        self.move(off[0], off[1])
        self.dock_state = "expanding"
        self.redraw()

        def done():
            self._animating = False
            self.dock_state = "expanded"

        self._animate_move(on[0], on[1], done, frm=off, fade=True)
        self._start_detail_tick()
        # 数据超过 2 分钟未刷新：展开时顺带拉取一次最新额度
        if (self.last_ok_time is None or
                (datetime.datetime.now() - self.last_ok_time).total_seconds() > 120):
            self.refresh_async()

    def _collapse_to_dock(self):
        """贴边展开状态下鼠标离开后，直线滑回收起。"""
        self._collapse_timer.stop()
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
        x, y = self.x(), self.y()
        sw, sh = self._screen_size()
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
            # 再重绘成把手 —— 收回全程直线滑出
            if side in ("left", "right"):
                self.move(slide[0], hy)
            else:
                self.move(hx, slide[1])
            self._apply_theme()
            self.redraw()

        self._animate_move(slide[0], slide[1], done, fade=True)

    def enterEvent(self, _ev):
        self._suppress_collapse = False
        if self.dock_state == "docked" and not self._animating:
            if self.cfg.get("edge_dock", True):
                self._expand_from_dock()   # 展开完整详情面板
            else:
                self._snap_to_expanded()   # 开关已关（手改配置等）：恢复普通窗口
        super().enterEvent(_ev)

    def _pointer_in_window(self, gp, tol=2):
        """指针全局坐标是否仍在窗口范围内（含贴屏幕边缘时的 ±tol 容差）。"""
        wx, wy = self.x(), self.y()
        ww, wh = self.width(), self.height()
        return (wx - tol <= gp.x() <= wx + ww + tol - 1 and
                wy - tol <= gp.y() <= wy + wh + tol - 1)

    def _recheck_pointer(self):
        """Leave 误报后的兜底轮询：指针真离开了就收回，防卡在展开态。"""
        if self.dock_state != "expanded" or self._animating:
            return
        inside = self._pointer_in_window(QCursor.pos())
        if inside or self._suppress_collapse:
            # 仍贴在窗口上（或菜单打开期间）：继续盯
            self._recheck_timer.start(300)
        else:
            self._collapse_to_dock()

    def leaveEvent(self, _ev):
        # 拖动进行中（按下左键有隐式抓取，快速拖动会触发 Leave）不收回
        if self._drag_offset is not None:
            return
        if self.dock_state == "expanded" and not self._animating:
            if self.cfg.get("edge_dock", True):
                # 面板边线与屏幕边缘重合时，指针顶到屏幕最边缘会被系统
                # 判成出界而误报 Leave；按指针实际位置复核
                if self._pointer_in_window(QCursor.pos()):
                    self._cancel_collapse_timer()
                    self._recheck_timer.start(300)
                    return
                self._cancel_collapse_timer()
                self._collapse_to_dock()   # 鼠标移开立即收回，不做延时
            else:
                self._snap_to_expanded()   # 开关已关：直接恢复普通窗口
        super().leaveEvent(_ev)

    def _clamp_start_pos(self):
        """窗口记忆位置跑出屏幕（改分辨率/换显示器）时复位。"""
        x = int(_to_int(self.cfg.get("window_x", 120), 120))
        y = int(_to_int(self.cfg.get("window_y", 120), 120))
        try:
            sw, sh = self._screen_size()
            w = int(300 * self.k)   # 详情面板宽度
            if x > sw - 60 or y > sh - 60 or x < 40 - w or y < 0:
                return 120, 120
        except Exception:
            pass
        return x, y

    # ---------------- 详情面板倒计时 ----------------

    def _start_detail_tick(self):
        """详情面板停留期间每 30 秒刷新倒计时文字。"""
        self._cancel_detail_tick()
        self._detail_timer.start(30000)

    def _detail_tick_cb(self):
        if self.dock_state in ("expanding", "expanded") and not self._animating:
            self.redraw()
            self._detail_timer.start(30000)

    # ---------------- 置顶守护 ----------------

    def _start_topmost_guard(self):
        """启动置顶守护：定时用 SetWindowPos 重新断言 HWND_TOPMOST。"""
        self._cancel_topmost_guard()
        self._topmost_timer.start(700)

    def _topmost_tick(self):
        if self._topmost_paused:
            # 右键菜单显示中：此时把悬浮窗钉回最上层会重新盖住菜单，先让位
            self._topmost_timer.start(400)
            return
        if not self._topmost_hwnd:
            self._topmost_hwnd = self._native_hwnd()
        win32.assert_topmost(self._topmost_hwnd)
        # 设置窗是悬浮窗的子窗；重新钉住悬浮窗后再把设置窗也提到最上，
        # 避免它被刚置顶的悬浮窗盖住
        dlg = self._settings_open
        try:
            if dlg is not None and dlg.isVisible():
                win32.assert_topmost(dlg._native_hwnd()
                                     if hasattr(dlg, "_native_hwnd")
                                     else int(dlg.winId()))
        except Exception:
            pass
        self._topmost_timer.start(1500)

    def _cancel_topmost_guard(self):
        self._topmost_timer.stop()

    # ---------------- 设置 ----------------

    def _needs_setup(self):
        z = self.cfg.get("zhipu", {})
        v = self.cfg.get("volcano", {})
        return is_placeholder(z.get("api_key")) and is_placeholder(v.get("access_key_id"))

    def open_settings(self):
        from .settings_dialog import SettingsDialog
        try:
            if self._settings_open is not None:
                self._settings_open.showNormal()
                self._settings_open.raise_()
                self._settings_open.activateWindow()
                return
        except RuntimeError:
            self._settings_open = None   # 底层 C++ 对象已销毁
        self._settings_open = SettingsDialog(
            self, self.cfg, self.apply_settings,
            (self.enable_autostart, self.disable_autostart),
            skin=self.theme_eff,
            on_update=self._manual_update_check,
            on_close=lambda: setattr(self, "_settings_open", None))
        self._settings_open.show()

    def apply_settings(self, patch):
        save_config_patch(patch)             # 增量写盘
        deep_merge(self.cfg, patch)          # 同步内存
        if self.cfg.get("burn_predict", False) and not self._hist:
            self._hist = load_history(8 * 86400)   # 刚打开烧速预测：补读历史
        self._restore_alpha()
        self._metrics()
        self._pick_fonts()
        self._build_menu()
        self._apply_theme()                  # 配色主题可能被修改
        self._placed = True                  # 保持当前位置，不跳回
        if not self.cfg.get("edge_dock", True) and self.dock_state == "docked":
            self._snap_to_expanded()       # 贴边开关被关闭：恢复普通窗口
        else:
            self.redraw()
        self.refresh_async()

    # ---------------- 开机自启 ----------------

    def enable_autostart(self):
        from core import autostart
        ok, msg = autostart.enable_autostart()
        self._toast(msg)

    def disable_autostart(self):
        from core import autostart
        ok, msg = autostart.disable_autostart()
        self._toast(msg)

    # ---------------- 更新 ----------------

    def _setup_updater(self):
        from core.updater import Updater
        from .update_ui import UpdateBubble, UpdateProgressDialog, UpdaterBridge

        self._update_bridge = UpdaterBridge()
        self.updater = Updater(
            APP_VERSION,
            on_prompt=self._update_bridge.prompt.emit,
            on_message=lambda t, m: self._update_bridge.message.emit(t, m),
            on_error=lambda t, m: self._update_bridge.error.emit(t, m),
            on_progress=self._update_bridge._on_progress,
            on_quit=self._update_bridge.quitApp.emit)
        self._update_bridge.prompt.connect(self._show_update_bubble)
        self._update_bridge.message.connect(self._update_message)
        self._update_bridge.error.connect(self._update_error)
        self._update_bridge.quitApp.connect(self.quit)
        self._progress_dlg = UpdateProgressDialog(self, self.updater)
        self._update_bridge.progressStarted.connect(
            self._progress_dlg.show_for)
        self._update_bridge.progressPct.connect(self._progress_dlg.set_progress)

    def _manual_update_check(self):
        """右键菜单「检查更新」：先给即时反馈，不允许点了毫无动静。"""
        if not self.updater:
            self._toast("当前运行方式不支持自动更新")
            return
        if self.updater.busy:
            self._toast("正在检查更新，请稍候…")
            return
        if not self.updater.pkg:
            self._toast("当前运行方式不支持自动更新")
            return
        self._toast("正在检查更新…")
        self.updater.check()

    def _silent_update_check(self):
        """后台静默检查更新。仅在打包版且未处于初始设置状态时执行。"""
        try:
            if not self.updater or self._needs_setup():
                return
            self.updater.check(silent=True)
        except Exception:
            pass

    def _update_message(self, title, text):
        self._toast(text.split("\n")[0][:60] or title)

    def _update_error(self, title, msg):
        self._toast("%s：%s" % (title, msg.split("\n")[0][:50]))

    def _show_update_bubble(self, info):
        """发现新版时在屏幕右下角弹出可点击气泡。"""
        try:
            from .update_ui import UpdateBubble
            bubble = UpdateBubble(info, self.t, self.font_family,
                                  self.uscale)
            bubble.show_at_bottom_right()
        except Exception:
            # 气泡兜底：至少用原生对话框把更新信息给出去，不能静默失败
            try:
                from PySide6.QtWidgets import QMessageBox
                box = QMessageBox(QMessageBox.Information, "发现新版本",
                                  "发现新版本 %s，是否现在下载并安装？" % info.get("tag"),
                                  QMessageBox.Yes | QMessageBox.No)
                if box.exec() == QMessageBox.Yes:
                    info["download"]()
                else:
                    info["dismiss"]()
            except Exception:
                pass

    # ---------------- 退出 ----------------

    def quit(self):
        self._cancel_collapse_timer()
        self._cancel_detail_tick()
        self._cancel_topmost_guard()
        if self._anim_group is not None:
            self._anim_group.stop()
            self._anim_group.deleteLater()
            self._anim_group = None
        self._refresh_timer.stop()
        self._drag_timer.stop()
        try:
            if self._hwnd_cache:
                core_notify._tray_icon_remove(self._hwnd_cache)
        except Exception:
            pass
        try:
            tray = getattr(self, "_tray", None)
            if tray is not None:
                tray.hide()
        except Exception:
            pass
        self._save_window_pos()
        self._quitting = True
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    # ---------------- 数据刷新 ----------------

    def refresh_async(self):
        if self.updating:
            return
        self._refresh_timer.stop()
        self.updating = True
        self.status_text = "更新中…"
        self.redraw()
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        # 每次刷新前重读磁盘配置：手动编辑 config.json 保存后，
        # 右键「立即刷新」即可生效，无需重启程序。
        # 注意：worker 线程只读快照，不写 self.cfg（UI 线程在用），
        # 合并动作挪到主线程 _apply_refresh 里做
        try:
            snap = load_config()
        except Exception:
            snap = {}
        zcfg = dict(snap.get("zhipu", {}) or {})
        vcfg = dict(snap.get("volcano", {}) or {})

        def _safe(fn, arg):
            def run():
                try:
                    return fn(arg)
                except Exception as e:
                    return {"ok": False, "error": str(e)[:80]}
            return run

        tasks = {}
        if zcfg.get("enabled", True):
            tasks["zhipu"] = _safe(_fetch_zhipu, zcfg)
        if vcfg.get("enabled", True):
            tasks["volcano"] = _safe(_fetch_volcano, vcfg)
        out = {}
        if tasks:
            # 两家供应商并行请求，刷新等待时间减半
            with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                futs = {name: pool.submit(fn) for name, fn in tasks.items()}
                for name, fut in futs.items():
                    out[name] = fut.result()
        self._bridge.done.emit(out.get("zhipu"), out.get("volcano"), snap)

    def _apply_refresh(self, zr, vr, snap=None):
        # 无论本轮处理是否异常，finally 里都要排下一轮刷新，
        # 否则任何一次绘制/通知抛异常都会让自动刷新链永久断裂
        try:
            if snap:
                deep_merge(self.cfg, snap)  # 主线程合并磁盘配置
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
                self._record_history()
                self._check_notifications()
            else:
                err = self.errors.get("zhipu") or self.errors.get("volcano") or "失败"
                self.status_text = "更新失败：%s" % err[:22]
            if self.isVisible():
                self.redraw()
        finally:
            # 手改 config.json 的脏值也不能断掉自动刷新链，故兜底解析
            minutes = _to_int(self.cfg.get("refresh_minutes", 5), 5, lo=1, hi=1440)
            self._refresh_timer.start(minutes * 60 * 1000)

    def _apply_visual_changes(self):
        """配置被外部修改（手改 config.json 后刷新）时同步视觉参数。"""
        try:
            if abs(_f(self.cfg.get("ui_scale", 1.0), 1.0) - self.uscale) > 1e-6:
                self._metrics()
                self._pick_fonts()
                self._build_menu()
            self._restore_alpha()
        except Exception:
            pass

    # ---------------- 历史记录 / 通知 ----------------

    def _usage_snapshot(self):
        """当前各窗口快照 [(供应商, 窗口名, 百分比, 重置时间戳), ...]。"""
        out = []
        zd = self.data.get("zhipu")
        if zd and zd.get("ok"):
            for nm, pct, rst in (zd.get("windows") or [])[:2]:
                out.append(("zhipu", nm, dp.safe_pct(pct), rst))
            mcp = zd.get("mcp")
            if mcp and mcp.get("percent") is not None:
                out.append(("zhipu", "MCP", dp.safe_pct(mcp["percent"]),
                            mcp.get("reset_ms")))
        vd = self.data.get("volcano")
        if vd and vd.get("ok"):
            for nm, used, quota, rst in (vd.get("windows") or [])[:3]:
                out.append(("volcano", nm, dp.safe_ratio_pct(used, quota), rst))
        return out

    def _record_history(self):
        """把本次各窗口百分比写入本地历史，并刷新内存快照。
        烧速预测关闭时不记录，避免无意义的落盘。"""
        if not self.cfg.get("burn_predict", False):
            return
        rec = {"ts": time.time()}
        for prov, nm, pct, _rst in self._usage_snapshot():
            key = HIST_KEY_MAP.get((prov, nm))
            if key and pct is not None:
                rec[key] = round(min(100.0, max(0.0, pct)), 1)
        if len(rec) > 1:
            append_history(rec)
            self._hist = load_history(8 * 86400)

    def _check_notifications(self):
        """阈值跨越 / 预测耗尽 / 临近重置的系统通知（逻辑在 core.notify）。"""
        if not self._hwnd_cache:
            self._hwnd_cache = self._native_hwnd()

        def _notify(title, text, warn=False):
            if self._hwnd_cache:
                core_notify.windows_notify(self._hwnd_cache, title, text,
                                           warn=warn)

        core_notify.check_notifications(self._usage_snapshot(), self._hist,
                                        self.cfg, self._notify_state, _notify)

    # ---------------- Toast ----------------

    def _toast(self, msg):
        """悬浮窗下方弹出小气泡，短暂停留后淡出。"""
        from .toast import show_toast
        show_toast(self, msg, self.t, max(9, int(round(9 * self.uscale))))


# 延迟导入供应商函数，便于单独替换/测试
from core.providers_volcano import fetch_volcano as _fetch_volcano  # noqa: E402
from core.providers_zhipu import fetch_zhipu as _fetch_zhipu  # noqa: E402
