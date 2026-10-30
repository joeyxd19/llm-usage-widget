# -*- coding: utf-8 -*-
"""悬浮窗与详情面板配色（色值与 v2 完全一致）。"""

C_BG        = "#17181d"   # 窗体背景（v2 透明色；v3 用 WA_TranslucentBackground 实现圆角）
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

# 深浅两套主题（键值与 v2 相同）
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

FONT_FAMILY_CANDIDATES = ["Microsoft YaHei UI", "Microsoft YaHei",
                          "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
