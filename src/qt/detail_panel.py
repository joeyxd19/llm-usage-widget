# -*- coding: utf-8 -*-
"""详情面板与贴边把手的布局与绘制。

从 v2 UsageWidget._detail_layout / _redraw_detail / _redraw_handle 移植：
块状布局算法（head/prov/win/stat/err/foot）与全部尺寸公式逐行保留，
tk Canvas 绘制换成 QPainter。布局函数为纯函数（数据/配置作参数），
绘制函数接收 QPainter 与主题/字体。"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen

from core.burn_predict import fmt_reset_info, predict_text
from core.config import _to_bool, _to_int

from .theme import C_ACCENT_V, C_ACCENT_Z


# --------------------------------------------------------------------------
# 数值安全转换与格式化（v2 的 staticmethod 原样剥离）
# --------------------------------------------------------------------------

def safe_pct(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def safe_ratio_pct(used, quota):
    """used/quota → 百分比；任一为非数值或 quota 非正时返回 None。

    接口字段没有类型保证，火山 windows 的 used/quota 若是字符串，
    直接相除会在绘制路径上抛 TypeError（画布已清空 → 白窗）。"""
    try:
        used, quota = float(used), float(quota)
    except (TypeError, ValueError):
        return None
    if quota <= 0:
        return None
    return round(used * 100.0 / quota, 1)


def fmt_num(n):
    try:
        n = float(n)
        if n >= 10000:
            return "%.1fw" % (n / 10000)
        if n == int(n):
            return str(int(n))
        return "%.1f" % n
    except Exception:
        return str(n)


def fmt_tokens(n):
    """token 总量：>=1B → x.xxB，>=1M → x.xxM，>=1K → x.xK，否则整数。"""
    try:
        n = float(n)
        if n >= 1e9:
            return "%.2fB" % (n / 1e9)
        if n >= 1e6:
            return "%.2fM" % (n / 1e6)
        if n >= 1e3:
            return "%.1fK" % (n / 1e3)
        return str(int(n))
    except Exception:
        return str(n)


# --------------------------------------------------------------------------
# 数据聚合
# --------------------------------------------------------------------------

def progress_groups(data, cfg):
    """返回 [(供应商名, 品牌色, [(进度名, 百分比), ...]), ...]，只含已启用的。"""
    groups = []
    if cfg.get("zhipu", {}).get("enabled", True):
        zd = data.get("zhipu")
        if zd and zd.get("ok"):
            groups.append(("智谱", C_ACCENT_Z,
                           [("5小时", zd.get("five_hour")),
                            ("本周", zd.get("weekly"))]))
        else:
            groups.append(("智谱", C_ACCENT_Z, []))
    if cfg.get("volcano", {}).get("enabled", True):
        vd = data.get("volcano")
        if vd and vd.get("ok"):
            wins = (vd.get("windows") or [])[:2]
            groups.append(("火山", C_ACCENT_V, [
                (nm, safe_ratio_pct(u, q))
                for nm, u, q, _ in wins]))
        else:
            groups.append(("火山", C_ACCENT_V, []))
    return groups


def color_for(t, cfg, percent):
    """按预警阈值取进度色。"""
    if percent is None:
        return t["dim"]
    try:
        percent = float(percent)
    except Exception:
        return t["dim"]
    if percent >= float(cfg.get("critical_percent", 95)):
        return t["crit"]
    if percent >= float(cfg.get("warn_percent", 80)):
        return t["warn"]
    return t["ok"]


def tag_bg(t, accent):
    if accent == C_ACCENT_Z:
        return t["tag_z"]
    if accent == C_ACCENT_V:
        return t["tag_v"]
    return t["track"]


# --------------------------------------------------------------------------
# 详情面板布局（与 v2 _detail_layout 同一尺寸公式）
# --------------------------------------------------------------------------

def layout_blocks(data, errors, cfg, hist, k):
    """构建详情面板内容块，返回 (w, h, blocks, pad)。
    blocks 高度含全部间距，绘制时按顺序消费即可保证与总高一致。"""
    w = int(300 * k)
    pad = int(12 * k)
    label_h = max(16, int(18 * k))
    line_h = max(14, int(15 * k))
    det_h = max(13, int(14 * k))
    bar_h = max(4, int(5 * k))
    win_h = label_h + bar_h + int(3 * k) + line_h + int(9 * k)

    blocks = [{"t": "head", "h": max(20, int(24 * k)) + int(6 * k)}]
    z_on = cfg.get("zhipu", {}).get("enabled", True)
    v_on = cfg.get("volcano", {}).get("enabled", True)

    def prov(name, accent, level):
        blocks.append({"t": "prov", "name": name, "accent": accent,
                       "level": (level or "").upper(), "h": max(20, int(24 * k))})

    if z_on:
        zd = data.get("zhipu") or {}
        prov("智谱 Coding Plan", C_ACCENT_Z, zd.get("level"))
        if zd.get("ok"):
            wins = zd.get("windows") or []
            if not wins:   # 旧结构数据兜底
                wins = [("5小时", zd.get("five_hour"), None),
                        ("本周", zd.get("weekly"), None)]
            for nm, pct, rst in wins[:2]:
                pct = safe_pct(pct)   # 接口字段无类型保证
                blk = {"t": "win",
                       "label": "5 小时窗口" if nm == "5小时" else "本周额度",
                       "pct": pct, "reset": rst, "used": "",
                       "h": win_h}
                pred = predict_text(cfg, hist, "zhipu", nm, pct, rst)
                if pred:
                    blk["pred"], blk["pred_c"] = pred
                    blk["h"] += line_h
                blocks.append(blk)
            mcp = zd.get("mcp")
            if mcp:
                blocks.append({
                    "t": "win", "label": "MCP 每月额度",
                    "pct": safe_pct(mcp.get("percent")),
                    "reset": mcp.get("reset_ms"),
                    "used": "已用 %s / %s" % (
                        fmt_num(mcp.get("used", 0)),
                        fmt_num(mcp.get("total", 0))),
                    "h": win_h + det_h, "details": mcp.get("details") or []})
            # 近 N 天累计 token 用量（独立接口，拿不到数据时整行隐藏）
            for lbl, key in (("近 30 天用量", "thirty_day"),
                             ("近 15 天用量", "fifteen_day"),
                             ("近 7 天用量", "seven_day")):
                td = zd.get(key)
                if td:
                    blocks.append({"t": "stat", "label": lbl,
                                   "text": "%s tokens · %s 次" % (
                                       fmt_tokens(td.get("tokens", 0)),
                                       fmt_num(td.get("calls", 0))),
                                   "h": line_h + int(6 * k)})
        else:
            blocks.append({"t": "err", "h": line_h + int(4 * k),
                           "text": "⚠ " + (errors.get("zhipu")
                                            or "暂无数据，右键 → 立即刷新")})

    if v_on:
        vd = data.get("volcano") or {}
        prov("火山 Agent Plan", C_ACCENT_V, vd.get("level"))
        if vd.get("ok"):
            for nm, used, quota, reset in (vd.get("windows") or [])[:3]:
                pct = safe_ratio_pct(used, quota)
                blk = {"t": "win", "label": str(nm) + "额度",
                       "pct": pct, "reset": reset,
                       "used": "已用 %s / %s" % (
                           fmt_num(used), fmt_num(quota)),
                       "h": win_h}
                pred = predict_text(cfg, hist, "volcano", nm, pct, reset)
                if pred:
                    blk["pred"], blk["pred_c"] = pred
                    blk["h"] += line_h
                blocks.append(blk)
        else:
            blocks.append({"t": "err", "h": line_h + int(4 * k),
                           "text": "⚠ " + (errors.get("volcano")
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


# --------------------------------------------------------------------------
# 绘制
# --------------------------------------------------------------------------

def _qcolor(hexcolor):
    try:
        return QColor(hexcolor)
    except Exception:
        return QColor("#888888")


def _draw_text(p, x, cy, text, font, color, align="l"):
    """垂直居中的单行文字（对齐 tk create_text 的 anchor 语义：
    "l" x 取左缘，"r" x 取右缘，"c" x 取中心）。"""
    p.setFont(font)
    p.setPen(_qcolor(color))
    fm = QFontMetrics(font)
    flags = {"l": Qt.AlignLeft, "r": Qt.AlignRight, "c": Qt.AlignHCenter}[align]
    if align == "l":
        rect = QRectF(x, cy - fm.height() / 2.0, 4096, fm.height())
    elif align == "r":
        rect = QRectF(x - fm.horizontalAdvance(text) - 2,
                      cy - fm.height() / 2.0,
                      fm.horizontalAdvance(text) + 2, fm.height())
    else:
        rect = QRectF(x - 100, cy - fm.height() / 2.0, 200, fm.height())
    p.drawText(rect, flags | Qt.AlignVCenter, text)


def _measure(font, text):
    return QFontMetrics(font).horizontalAdvance(text)


def _rrect(p, x0, y0, x1, y1, r, fill=None, outline=None, width=1):
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(_qcolor(outline)) if outline else QPen(Qt.NoPen)
    pen.setWidthF(width)
    p.setPen(pen if outline else QPen(Qt.NoPen))
    p.setBrush(_qcolor(fill) if fill else Qt.NoBrush)
    p.drawRoundedRect(QRectF(x0, y0, x1 - x0, y1 - y0), r, r)


def paint_detail(p, w, h, blocks, pad, t, cfg, fonts, radius, status_right,
                 updating, last_ok_time, k, app_version, font_family):
    """完整详情面板（主窗口 / 贴边悬停展开共用）：
    各窗口额度、重置时间、用量明细。"""
    p.setRenderHint(QPainter.Antialiasing, True)
    _rrect(p, 2, 2, w - 2, h - 2, radius, fill=t["card"], outline=t["line"])
    bar_h = max(4, int(5 * k))
    line_h = max(14, int(15 * k))
    f_title, f_text, f_small, f_pct = (fonts["title"], fonts["text"],
                                       fonts["small"], fonts["pct"])
    y = float(pad)

    for b in blocks:
        bt = b["t"]
        if bt == "head":
            cy = y + b["h"] / 2.0 - int(3 * k)
            _draw_text(p, pad, cy, "额度详情", f_title, t["text"])
            _draw_text(p, w - pad, cy, status_right, f_small, t["dim"], "r")
            ly = y + b["h"] - int(6 * k)
            p.setPen(QPen(_qcolor(t["line"]), 1))
            p.drawLine(QPointF(pad, ly), QPointF(w - pad, ly))
            y += b["h"]
        elif bt == "prov":
            if b.get("sep"):
                p.setPen(QPen(_qcolor(t["line"]), 1))
                p.drawLine(QPointF(pad, y + int(5 * k)),
                           QPointF(w - pad, y + int(5 * k)))
                y += int(13 * k)
            cy = y + b["h"] / 2.0
            dot_r = max(2, int(2.5 * k))
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(b["accent"]))
            p.drawEllipse(QPointF(pad + dot_r, cy), dot_r, dot_r)
            tx = pad + 2 * dot_r + int(5 * k)
            _draw_text(p, tx, cy, b["name"], f_title, t["text"])
            if b["level"]:
                lx = tx + _measure(f_title, b["name"]) + int(6 * k)
                lw = _measure(f_small, b["level"]) + int(8 * k)
                p.setPen(Qt.NoPen)
                p.setBrush(_qcolor(tag_bg(t, b["accent"])))
                p.drawRect(QRectF(lx, cy - int(8 * k), lw, int(16 * k)))
                _draw_text(p, lx + int(4 * k), cy, b["level"], f_small, t["text"])
            y += b["h"]
        elif bt == "win":
            label_h = max(16, int(18 * k))
            cy = y + label_h / 2.0
            _draw_text(p, pad, cy, b["label"], f_text, t["dim"])
            pct = b["pct"]
            pct_txt = "%d%%" % round(float(pct)) if pct is not None else "—"
            _draw_text(p, w - pad, cy, pct_txt, f_pct,
                       color_for(t, cfg, pct), "r")
            y += label_h
            # 进度条
            _rrect(p, pad, y, w - pad, y + bar_h, bar_h / 2.0,
                   fill=t["track"], outline=t["track"])
            if pct is not None and float(pct) > 0:
                fw = (w - 2 * pad) * min(100.0, float(pct)) / 100.0
                cc = color_for(t, cfg, pct)
                _rrect(p, pad, y, pad + fw, y + bar_h, bar_h / 2.0,
                       fill=cc, outline=cc)
            y += bar_h + int(3 * k)
            # 用量 + 重置时间
            cd, abs_s = fmt_reset_info(b["reset"])
            seg = b["used"]
            for extra in (cd, abs_s):
                if extra:
                    seg = (seg + " · " if seg else "") + extra
            _draw_text(p, pad, y + line_h / 2.0, seg or "—", f_small, t["dim"])
            y += line_h
            if b.get("pred"):
                _draw_text(p, pad, y + line_h / 2.0, b["pred"], f_small,
                           t.get(b["pred_c"], t["dim"]))
                y += line_h
            if b.get("details"):
                det_h = max(13, int(14 * k))
                dtxt = " · ".join("%s %s" % (nm, fmt_num(uv))
                                  for nm, uv in b["details"][:4])
                _draw_text(p, pad, y + det_h / 2.0, dtxt, f_small, t["dim"])
                y += det_h
            y += int(9 * k)
        elif bt == "stat":
            # 单行统计（左标签右数值），无进度条、无重置时间
            cy = y + b["h"] / 2.0
            _draw_text(p, pad, cy, b["label"], f_text, t["dim"])
            _draw_text(p, w - pad, cy, b["text"], f_text, t["dim"], "r")
            y += b["h"]
        elif bt == "err":
            _draw_text(p, pad, y + b["h"] / 2.0, b["text"][:34], f_small,
                       t["warn"])
            y += b["h"]
        elif bt == "foot":
            p.setPen(QPen(_qcolor(t["line"]), 1))
            p.drawLine(QPointF(pad, y + int(2 * k)),
                       QPointF(w - pad, y + int(2 * k)))
            cy = y + int(4 * k) + max(16, int(20 * k)) / 2.0
            # 脏配置容错：手改 config.json 的字符串数字不能让绘制路径抛异常
            rm = _to_int(cfg.get("refresh_minutes", 5), 5, lo=1, hi=1440)
            _draw_text(p, pad, cy,
                       "每 %d 分钟自动刷新 · 右键可立即刷新" % rm,
                       f_small, t["dim"])
            _draw_text(p, w - pad, cy, "v" + app_version, f_small, t["dim"], "r")


def handle_geometry(win_w, win_h, side, thick, margin):
    """胶囊把手在把手窗内的矩形 (x0, y0, x1, y1)。

    贴边侧与窗口对应边重合（直角贴屏幕边缘），其余三侧留 margin 透明
    余量，作为悬停 1.05 微放大的出血位。"""
    if side == "left":
        return 0.0, float(margin), float(thick), float(win_h) - margin
    if side == "right":
        return (float(win_w) - thick, float(margin),
                float(win_w), float(win_h) - margin)
    if side == "top":
        return float(margin), 0.0, float(win_w) - margin, float(thick)
    return (float(margin), float(win_h) - thick,
            float(win_w) - margin, float(win_h))


def paint_handle(p, win_w, win_h, side, thick, margin, k, t, cfg, data,
                 scale=1.0):
    """贴边收起把手（v2 经典样式）：边缘一条细长全圆角胶囊，内嵌各供应商
    微型进度条（每家最多两条：5 小时 / 本周窗口），底槽 track、填充按
    预警阈值取色（<warn 绿 / >=warn 黄 / >=crit 红，无数据退灰）。

    - 悬停微放大（scale，锚点为贴边面中点，鼓出方向全在出血余量里）；
    - edge_glow 开启时用亮一档的 handle_edge 描边配合呼吸光效，
      关闭时退回普通卡片描边的静态胶囊；
    - 进度条与 v2 _redraw_handle 同一尺寸公式（bar 3.5k、间距 2k）。"""
    p.setRenderHint(QPainter.Antialiasing, True)
    x0, y0, x1, y1 = handle_geometry(win_w, win_h, side, thick, margin)
    vertical = side in ("left", "right")
    if vertical:
        ax, ay = (x0 if side == "left" else x1), (y0 + y1) / 2.0
        axis0, axis_len = y0, y1 - y0
    else:
        ax, ay = (x0 + x1) / 2.0, (y0 if side == "top" else y1)
        axis0, axis_len = x0, x1 - x0

    # 悬停放大的锚点 = 贴边面中点：贴边侧不动，鼓出方向全在出血余量里
    p.save()
    p.translate(ax, ay)
    s = max(0.5, min(2.0, float(scale)))
    p.scale(s, s)
    p.translate(-ax, -ay)

    glow = _to_bool(cfg.get("edge_glow", True), True)
    edge = t.get("handle_edge", t["line"]) if glow else t["line"]
    p.setPen(QPen(_qcolor(edge), 1))
    p.setBrush(_qcolor(t["card"]))
    pill = min(x1 - x0, y1 - y0) / 2.0   # 全圆角胶囊（与 v2 一致）
    path = QPainterPath()
    path.addRoundedRect(QRectF(x0, y0, x1 - x0, y1 - y0), pill, pill)
    p.drawPath(path)

    # ---- 内容：每家供应商一组微型进度条（进度条走像素对齐，关掉抗锯齿更脆） ----
    p.setRenderHint(QPainter.Antialiasing, False)
    p.setPen(Qt.NoPen)
    groups = progress_groups(data, cfg)
    if not groups:
        d = max(4, int(5 * k))   # 没有已启用供应商：居中一个小灰点
        p.setBrush(_qcolor(t["track"]))
        p.drawRect(QRectF((x0 + x1) / 2.0 - d / 2.0,
                          (y0 + y1) / 2.0 - d / 2.0, d, d))
    else:
        n = len(groups)
        bar_len = max(2, int(3.5 * k))     # 微型进度条厚度轴宽
        bar_gap = max(1, int(2 * k))       # 同组两条进度条的间距
        slots_n = 2                        # 每家画两条（5 小时 / 本周）
        if vertical:
            pad, gap = int(5 * k), int(6 * k)
            seg = (axis_len - 2 * pad - gap * (n - 1)) / float(n)
            total_w = slots_n * bar_len + (slots_n - 1) * bar_gap
            bx = x0 + (thick - total_w) / 2.0   # 沿厚度轴居中
            for gi, (_nm, _acc, bars) in enumerate(groups):
                y0g = axis0 + pad + gi * (seg + gap)
                items = list(bars[:slots_n])
                items += [None] * (slots_n - len(items))
                for bi, item in enumerate(items):
                    xx = bx + bi * (bar_len + bar_gap)
                    p.setBrush(_qcolor(t["track"]))
                    p.drawRect(QRectF(xx, y0g, bar_len, seg))
                    if item and item[1] is not None:
                        try:
                            pct = max(0.0, min(100.0, float(item[1])))
                        except (TypeError, ValueError):
                            continue
                        ph = seg * pct / 100.0
                        if ph >= 1:
                            p.setBrush(_qcolor(color_for(t, cfg, pct)))
                            p.drawRect(QRectF(xx, y0g + seg - ph, bar_len, ph))
        else:
            pad, gap = int(6 * k), int(6 * k)
            seg = (axis_len - 2 * pad - gap * (n - 1)) / float(n)
            total_h = slots_n * bar_len + (slots_n - 1) * bar_gap
            by = y0 + (thick - total_h) / 2.0
            for gi, (_nm, _acc, bars) in enumerate(groups):
                x0g = axis0 + pad + gi * (seg + gap)
                items = list(bars[:slots_n])
                items += [None] * (slots_n - len(items))
                for bi, item in enumerate(items):
                    yy = by + bi * (bar_len + bar_gap)
                    p.setBrush(_qcolor(t["track"]))
                    p.drawRect(QRectF(x0g, yy, seg, bar_len))
                    if item and item[1] is not None:
                        try:
                            pct = max(0.0, min(100.0, float(item[1])))
                        except (TypeError, ValueError):
                            continue
                        pw = seg * pct / 100.0
                        if pw >= 1:
                            p.setBrush(_qcolor(color_for(t, cfg, pct)))
                            p.drawRect(QRectF(x0g, yy, pw, bar_len))
    p.restore()
