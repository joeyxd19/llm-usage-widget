# -*- coding: utf-8 -*-
"""配置读写：默认值 + 磁盘增量合并，键名与 v2 完全一致。

容错原则：config.json 支持用户手动编辑，脏值（字符串数字、None、越界值、
未知键）绝不能让刷新链、UI 等关键路径抛异常。load_config 原样搬运磁盘值
（不越权修正），各使用点用 to_int / min-max 自行收敛；保存走"读盘-合并-写回"
的增量模式，避免运行中的旧内存配置覆盖用户刚手改的密钥。
"""

import json
import os
import shutil
import sys

APP_NAME = "额度悬浮窗"
APP_VERSION = "3.1.1"
CONFIG_NAME = "config.json"
HISTORY_NAME = "history.jsonl"   # 本地用量历史（每次刷新一条快照，烧速预测用）
HISTORY_KEEP_DAYS = 14           # 历史保留天数

DEFAULT_CONFIG = {
    "zhipu": {
        "enabled": True,
        "api_key": "在这里填你的智谱API_Key",
        "base_url": "open.bigmodel.cn",
        "show_30d": True,        # 详情面板显示近 30 天 token 用量
        "show_15d": True,        # 详情面板显示近 15 天 token 用量
        "show_7d": True,         # 详情面板显示近 7 天 token 用量
    },
    "volcano": {
        "enabled": True,
        "access_key_id": "在这里填火山AccessKeyID_AKLT开头",
        "secret_access_key": "在这里填火山SecretAccessKey",
        "region": "cn-beijing",
    },
    "refresh_minutes": 5,
    "opacity": 0.96,
    "ui_scale": 1.0,
    "warn_percent": 80,
    "critical_percent": 95,
    "notify": True,             # 系统通知预警（阈值跨越/临近重置）
    "burn_predict": False,      # 烧速预测（按历史用量外推何时用尽），默认关闭
    "window_x": 120,
    "window_y": 120,
    "theme": "auto",           # auto 自动跟随背景 | dark 深色 | light 浅色
    "edge_dock": False,        # 贴边自动收起（默认关：新用户先看到完整悬浮窗；老配置里的 true 保持 true）
    "edge_glow": True,         # 收起把手光效（1px 亮描边 + 呼吸动画；关了就是纯色静态条）
    "dock_side": "none",       # none/left/right/top/bottom（上次收起的边）
    "dock_len": 150,           # 贴边长条默认长度（px，40~240）
    "dock_hint_shown": False,  # 首次贴边收起的「把手在哪」提示已展示过
}

SCALE_STEPS = [0.75, 0.9, 1.0, 1.15, 1.3]
SCALE_LABELS = {
    0.75: "75%（紧凑）",
    0.9: "90%",
    1.0: "100%（标准）",
    1.15: "115%",
    1.3: "130%（大）",
}
# 设置窗口分段选择器用的短标签（与 SCALE_STEPS 一一对应）
SCALE_SHORT = {0.75: "75%", 0.9: "90%", 1.0: "100%", 1.15: "115%", 1.3: "130%"}


def _source_dir():
    """源码运行时的数据目录锚点：src/ 目录（与 v2 usage_widget.pyw 一致）。

    本文件位于 src/core/config.py，向上两级即 src/。源码模式下的
    config.json / history.jsonl / error.log 仍与 v2 放在同一位置，
    v3 切换不丢用户数据。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def app_dir():
    """程序所在目录（安装位置由用户在安装时决定）。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return os.path.dirname(sys.executable)
    return _source_dir()


DATA_DIR_NAME = "LLM Usage Widget"
_migrated_legacy = False


def data_dir():
    """用户数据目录（config.json / history.jsonl / error.log）。

    打包版固定放在 %APPDATA%\\LLM Usage Widget，与安装位置解耦：
    重装、换目录、升级都不会丢配置。源码运行仍用 src/ 目录，便于调试。
    首次运行时若旧版数据还在程序目录，会自动复制过来。"""
    global _migrated_legacy
    if not getattr(sys, "frozen", False):
        return _source_dir()

    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    target = os.path.join(base, DATA_DIR_NAME)
    try:
        os.makedirs(target, exist_ok=True)
    except Exception:
        return app_dir()

    if not _migrated_legacy:
        _migrated_legacy = True
        _migrate_legacy_data(target)
    return target


def _migrate_legacy_data(target):
    """把旧版放在程序目录里的用户数据搬到新位置（不覆盖已有文件）。"""
    legacy = app_dir()
    if os.path.abspath(target) == os.path.abspath(legacy):
        return
    for name in (CONFIG_NAME, HISTORY_NAME, "error.log"):
        src = os.path.join(legacy, name)
        dst = os.path.join(target, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass


def config_path():
    return os.path.join(data_dir(), CONFIG_NAME)


def _deep_merge(dst, src):
    """递归合并 src 到 dst（dict 深合并，其余直接覆盖）。"""
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


# 对外统一叫 deep_merge（UI 层合并 patch 也用它）；保留旧名兼容 v2 内部调用习惯
deep_merge = _deep_merge


def _load_file_config():
    """只读磁盘文件里的原始配置（不合并默认值），失败返回空 dict。"""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config():
    """默认配置 + 磁盘用户配置，返回运行用完整配置。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    user = _load_file_config()
    for section in ("zhipu", "volcano"):
        if isinstance(user.get(section), dict):
            cfg[section].update(user[section])
    for key in ("refresh_minutes", "opacity", "ui_scale", "warn_percent",
                "critical_percent", "notify", "burn_predict", "window_x",
                "window_y", "theme", "edge_dock", "edge_glow", "dock_side",
                "dock_len", "dock_hint_shown"):
        if key in user:
            cfg[key] = user[key]
    return cfg


def save_config_patch(patch):
    """
    增量保存：先读磁盘最新内容（用户可能刚手动编辑过），合并 patch 后写回。
    从根本上避免"运行中的旧内存配置覆盖用户刚填的密钥"。
    """
    base = _load_file_config()
    if not base:
        base = json.loads(json.dumps(DEFAULT_CONFIG))
    _deep_merge(base, patch)
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def is_placeholder(value):
    return (not value) or ("这里填" in str(value)) or str(value).strip() == ""


def _to_int(value, default, lo=None, hi=None):
    """宽松地把配置值取整，失败或越界时收敛到默认值。

    config.json 支持手动编辑，脏值（如 "abc"）不能让它炸掉刷新链等
    关键路径；lo/hi 给定时把结果夹在该区间内。"""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


# 对外统一叫 to_int；保留旧名兼容 v2 内部调用习惯
to_int = _to_int


def _to_bool(value, default=False):
    """宽松地把配置值转成 bool，识别不了的收敛到 default。

    config.json 支持手动编辑，"false"、"0" 这类字符串若用裸 bool()
    会被当成开启（非空字符串即真），呼吸光效这类开关会关不掉。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off", ""):
            return False
    return default


# 对外统一叫 to_bool
to_bool = _to_bool
