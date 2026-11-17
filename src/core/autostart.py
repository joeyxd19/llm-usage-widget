# -*- coding: utf-8 -*-
"""开机自启：在 Windows 启动目录写/删一个静默启动 VBS。

原 UsageWidget.enable_autostart / disable_autostart 剥离：核心是 VBS
路径与内容的生成，UI 层负责把返回的消息用气泡提示出来（v2 是直接
调 self._toast，剥离后改为返回消息字符串）。"""

import os
import sys

AUTOSTART_VBS_NAME = "usage_widget.vbs"


def autostart_file():
    """启动目录里的 VBS 完整路径；非 Windows 返回 None。"""
    if os.name != "nt":
        return None
    base = os.path.join(os.environ.get("APPDATA", ""),
                        "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")
    return os.path.join(base, AUTOSTART_VBS_NAME)


def quote_command(path):
    """把一个可执行文件路径包成 VBScript 里带转义引号的命令片段。

    整条命令包在一对引号里，内部路径的引号用两个连续引号转义，
    避免 VBScript 语法错误。"""
    return '""%s""' % path


def vbs_content(cmd):
    """生成静默启动 VBScript。cmd 为已转义引号的完整命令串，
    打包版是 '"<exe>"'，源码版是 '"<pythonw>" "<脚本>"'。

    窗口样式用 1（正常）而不是 0（隐藏）：Windows 会把启动信息里的隐藏
    状态套到进程第一次 ShowWindow 上，GUI 程序可能因此"窗口已显示"却
    始终不可见。两版入口都是 GUI 子系统，用 1 不会闪控制台。"""
    return 'CreateObject("WScript.Shell").Run "%s", 1, False\n' % cmd


def launch_command():
    """自启要运行的完整命令：打包版是 EXE 本身；源码版是入口脚本，
    且 python.exe 启动时换 pythonw.exe，避免自启时弹出控制台。"""
    if getattr(sys, "frozen", False):
        return quote_command(os.path.abspath(sys.executable))
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pw):
            exe = pw
    script = os.path.abspath(sys.argv[0] or __file__)
    return "%s %s" % (quote_command(exe), quote_command(script))


def enable_autostart():
    """写启动 VBS。返回 (是否成功, 提示消息)。"""
    path = autostart_file()
    if not path:
        return False, "开机自启仅支持 Windows"
    content = vbs_content(launch_command())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            # WScript 按系统 ANSI(GBK) 读取 .vbs，二进制写入避免中文路径乱码
            f.write(content.encode("gbk", "replace"))
        return True, "已设置开机自启"
    except Exception as e:
        return False, "设置失败: %s" % e


def disable_autostart():
    """删启动 VBS。返回 (是否删除, 提示消息)。"""
    path = autostart_file()
    if path and os.path.exists(path):
        try:
            os.remove(path)
            return True, "已取消开机自启"
        except Exception as e:
            return False, "取消失败: %s" % e
    return False, "当前没有开机自启"


def repair_autostart():
    """自启项指向别的 exe 时改写成当前程序，返回 (是否改写, 提示消息)。

    升级到 v3 或换安装目录后，启动目录里的旧 VBS 会继续拉起旧版：
    旧版没有常驻托盘、窗口还可能落在屏外，表现成"进程在跑但桌面上
    什么都看不到"。这里只在用户已经开过自启（VBS 存在）时改写，
    绝不新增自启项；内容只差窗口样式（旧版是 0）也一并收敛。"""
    if not getattr(sys, "frozen", False):
        return False, ""
    path = autostart_file()
    if not path or not os.path.isfile(path):
        return False, ""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("gbk", "replace")
    except OSError:
        return False, ""
    expected = vbs_content(launch_command())
    if text.replace("\r\n", "\n").strip() == expected.strip():
        return False, ""
    return enable_autostart()
