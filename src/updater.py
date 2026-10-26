# -*- coding: utf-8 -*-
"""应用内自动更新器。

查询 GitHub Releases，下载对应资产，然后派生一个独立的 PowerShell 脚本
完成"等当前进程退出 → 替换文件 → 重启"的流程。Windows 下运行中的 exe
被系统锁定，无法原地覆盖，必须由外部进程代劳。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.request
import urllib.error


GITHUB_API = (
    "https://api.github.com/repos/joeyxd19/llm-usage-widget/releases/latest"
)
APP_NAME = "额度悬浮窗"
AUTOSTART_VBS_NAME = "usage_widget.vbs"

# Windows 下创建脱离控制台的子进程；Linux/macOS 上无此常量， guard 一下
if os.name == "nt":
    _DETACHED_FLAGS = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
else:
    _DETACHED_FLAGS = 0


def is_frozen():
    return getattr(sys, "frozen", False)


def package_type():
    """返回当前运行形态："installed" / "onedir" / "onefile" / None（源码）。

    installed 指通过 Inno Setup 安装到 %LocalAppData% 的版本，目录里会
    有 unins000.exe；onedir 指手动解压的便携文件夹版。
    """
    if not is_frozen():
        return None
    exe_dir = os.path.dirname(sys.executable)
    has_internal = os.path.isdir(os.path.join(exe_dir, "_internal"))
    has_unins = os.path.isfile(os.path.join(exe_dir, "unins000.exe"))
    if has_internal and has_unins:
        return "installed"
    if has_internal:
        return "onedir"
    return "onefile"


def parse_version(tag):
    """把 'v2.6.4' 或 '2.6.4' 解析成 (2, 6, 4)。"""
    if tag and tag.lower().startswith("v"):
        tag = tag[1:]
    try:
        return tuple(int(x) for x in tag.split("."))
    except Exception:
        return (0, 0, 0)


def version_gt(a, b):
    return a > b


def _ps_escape(s):
    """转义成 PowerShell 单引号字符串安全形式。"""
    return s.replace("'", "''")


class Updater:
    def __init__(self, root, current_version):
        self.root = root
        self.current_version = current_version
        self.pkg = package_type()
        self._busy = False
        self._window = None
        self._progress = None
        self._label = None
        self._cancelled = False

    # ---------------- UI 入口 ----------------

    def check(self, silent=False):
        """启动检查更新线程。"""
        if not self.pkg:
            if not silent:
                messagebox.showinfo("检查更新", "源码运行模式不支持自动更新。")
            return
        if self._busy:
            return
        self._busy = True
        self._cancelled = False
        threading.Thread(
            target=self._check_worker, args=(silent,), daemon=True
        ).start()

    def _check_worker(self, silent):
        try:
            tag, body, assets = self._fetch_latest()
        except Exception as exc:
            self._ui_after(
                0, lambda: self._on_error("检查更新失败", str(exc), silent)
            )
            return

        new_ver = parse_version(tag)
        cur_ver = parse_version(self.current_version)
        if not version_gt(new_ver, cur_ver):
            self._busy = False
            if not silent:
                self._ui_after(
                    0,
                    lambda: messagebox.showinfo(
                        "检查更新", f"当前已是最新版本 v{self.current_version}。"
                    ),
                )
            return

        asset_name = self._asset_name(tag)
        asset = next(
            (a for a in assets if a.get("name") == asset_name), None
        )
        if asset is None:
            self._busy = False
            self._ui_after(
                0,
                lambda: self._on_error(
                    "未找到更新包",
                    f"Release {tag} 中没有 {asset_name}，请去 Releases 页面手动下载。",
                    silent,
                ),
            )
            return

        url = asset["browser_download_url"]
        size = asset.get("size", 0)
        self._ui_after(0, lambda: self._ask_update(tag, body, url, size))

    def _ask_update(self, tag, body, url, size):
        try:
            mb = size / (1024 * 1024) if size else 0
            size_text = f"约 {mb:.1f} MB" if mb else "大小未知"
            notes = (body or "暂无更新说明。").strip()
            if len(notes) > 400:
                notes = notes[:400] + "…"

            msg = (
                f"发现新版本：v{self.current_version} → {tag}\n\n"
                f"更新包：{self._asset_name(tag)}（{size_text}）\n\n"
                f"更新说明：\n{notes}\n\n"
                f"确定要下载并安装吗？安装时程序会自动退出并重启。"
            )
            if messagebox.askyesno("检查更新", msg):
                self._download_and_apply(tag, url)
            else:
                self._busy = False
        except Exception:
            self._busy = False

    def _download_and_apply(self, version, url):
        self._show_progress(f"正在下载 v{version} …")
        threading.Thread(
            target=self._download_worker, args=(version, url), daemon=True
        ).start()

    def _show_progress(self, text):
        if self._window is not None and self._window.winfo_exists():
            self._label.configure(text=text)
            return

        self._window = tk.Toplevel(self.root)
        self._window.title("更新")
        self._window.resizable(False, False)
        try:
            self._window.transient(self.root)
            self._window.attributes("-topmost", True)
        except Exception:
            pass
        self._window.geometry("360x90")
        self._window.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._label = tk.Label(self._window, text=text)
        self._label.pack(pady=(14, 6))
        self._progress = ttk.Progressbar(
            self._window, mode="determinate", maximum=100
        )
        self._progress.pack(fill="x", padx=24, pady=(0, 12))

    def _on_cancel(self):
        self._cancelled = True
        if self._window and self._window.winfo_exists():
            self._window.destroy()

    def _set_progress(self, pct, text=None):
        if self._progress and self._progress.winfo_exists():
            self._progress["value"] = pct
        if self._label and self._label.winfo_exists():
            if text:
                self._label.configure(text=text)
            else:
                self._label.configure(text=f"{pct}%")

    def _on_error(self, title, msg, silent):
        self._busy = False
        if self._window and self._window.winfo_exists():
            self._window.destroy()
        if not silent:
            messagebox.showerror(title, msg)

    def _ui_after(self, delay, fn):
        try:
            self.root.after(delay, fn)
        except Exception:
            pass

    # ---------------- 网络 ----------------

    def _asset_name(self, tag):
        version = tag[1:] if tag and tag.lower().startswith("v") else tag
        if self.pkg in ("onedir", "installed"):
            return f"llm-usage-widget_v{version}_update.zip"
        return f"llm-usage-widget_v{version}.exe"

    def _fetch_latest(self):
        req = urllib.request.Request(
            GITHUB_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": APP_NAME,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["tag_name"], data.get("body", ""), data.get("assets", [])

    def _download_worker(self, version, url):
        ext = ".zip" if self.pkg == "onedir" else ".exe"
        dest = os.path.join(
            tempfile.gettempdir(),
            f"llm-usage-widget_update_v{version}{ext}",
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                block = 64 * 1024
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(block)
                        if not chunk:
                            break
                        if self._cancelled:
                            raise RuntimeError("已取消下载")
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded * 100 // total
                            self._ui_after(
                                0, lambda p=pct: self._set_progress(p)
                            )
            self._ui_after(0, lambda: self._on_downloaded(version, dest))
        except Exception as exc:
            self._ui_after(
                0, lambda: self._on_error("下载失败", str(exc), False)
            )

    def _on_downloaded(self, version, dest):
        self._set_progress(100, "下载完成，准备安装…")
        try:
            self._spawn_updater(version, dest)
        except Exception as exc:
            self._on_error("更新失败", f"无法启动安装程序：{exc}", False)
            return
        # 给安装脚本留时间启动，然后退出自身
        self._ui_after(800, self._quit_app)

    def _quit_app(self):
        try:
            self.root.destroy()
        except Exception:
            os._exit(0)

    # ---------------- 安装脚本 ----------------

    def _spawn_updater(self, version, dest):
        if os.name != "nt":
            raise RuntimeError("自动更新仅支持 Windows 打包版")

        exe = os.path.abspath(sys.executable)
        app_dir = os.path.dirname(exe)
        pid = os.getpid()

        if self.pkg == "onedir":
            ps = self._build_ps_onedir(pid, app_dir, dest)
        else:
            ps = self._build_ps_onefile(pid, exe, dest)

        ps_path = os.path.join(
            tempfile.gettempdir(), "llm-usage-widget-updater.ps1"
        )
        with open(ps_path, "w", encoding="utf-8") as f:
            f.write(ps)

        cmd = [
            "powershell.exe",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            ps_path,
        ]
        subprocess.Popen(
            cmd,
            shell=False,
            creationflags=_DETACHED_FLAGS,
            close_fds=True,
        )

    def _autostart_vbs_path(self):
        return os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft",
            "Windows",
            "Start Menu",
            "Programs",
            "Startup",
            AUTOSTART_VBS_NAME,
        )

    def _vbs_content(self, exe_path):
        """生成与 UsageWidget.enable_autostart 一致的 VBScript 内容。"""
        cmd = '""%s""' % exe_path
        return 'CreateObject("WScript.Shell").Run "%s", 0, False\n' % cmd

    def _build_ps_onedir(self, pid, app_dir, zip_path):
        app = _ps_escape(app_dir)
        zip_ = _ps_escape(zip_path)
        temp_dir = _ps_escape(
            os.path.join(tempfile.gettempdir(), "llm-usage-widget-update")
        )
        exe_name = "llm-usage-widget.exe"
        new_exe = _ps_escape(os.path.join(app_dir, exe_name))
        vbs_path = _ps_escape(self._autostart_vbs_path())
        error_log = _ps_escape(
            os.path.join(tempfile.gettempdir(), "llm-usage-widget-update-error.log")
        )

        return f"""try {{
    $ErrorActionPreference = 'Stop'
    $pidToWait = {pid}
    Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800

    $appDir = '{app}'
    $zip = '{zip_}'
    $tempDir = '{temp_dir}'
    $exeName = '{exe_name}'

    if (Test-Path $tempDir) {{
        Remove-Item -Path $tempDir -Recurse -Force
    }}
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

    Expand-Archive -Path $zip -DestinationPath $tempDir -Force
    $newDir = Join-Path $tempDir 'llm-usage-widget'

    $oldInternal = Join-Path $appDir '_internal'
    $newInternal = Join-Path $newDir '_internal'
    if (Test-Path $oldInternal) {{
        Remove-Item -Path $oldInternal -Recurse -Force
    }}
    Copy-Item -Path $newInternal -Destination $appDir -Recurse -Force

    $oldExe = Join-Path $appDir $exeName
    if (Test-Path $oldExe) {{
        Remove-Item -Path $oldExe -Force
    }}
    Copy-Item -Path (Join-Path $newDir $exeName) -Destination $appDir -Force

    Get-ChildItem -Path $appDir -Filter 'llm-usage-widget_v*.exe' | Remove-Item -Force

    $vbs = '{vbs_path}'
    if (Test-Path $vbs) {{
        $cmd = '""' + $oldExe + '""'
        $content = 'CreateObject(\"WScript.Shell\").Run \"' + $cmd + '\", 0, False' + "`n"
        [System.IO.File]::WriteAllText($vbs, $content, [System.Text.Encoding]::Default)
    }}

    Remove-Item -Path $tempDir -Recurse -Force
    Remove-Item -Path $zip -Force
    Start-Process -FilePath $oldExe -WorkingDirectory $appDir
}} catch {{
    $_.Exception.Message + '`n' + $_.ScriptStackTrace | Out-File -FilePath '{error_log}' -Encoding utf8
}} finally {{
    Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
"""

    def _build_ps_onefile(self, pid, old_exe, new_exe_temp):
        old = _ps_escape(old_exe)
        new_ = _ps_escape(new_exe_temp)
        app_dir = _ps_escape(os.path.dirname(old_exe))
        vbs_path = _ps_escape(self._autostart_vbs_path())
        error_log = _ps_escape(
            os.path.join(tempfile.gettempdir(), "llm-usage-widget-update-error.log")
        )

        return f"""try {{
    $ErrorActionPreference = 'Stop'
    $pidToWait = {pid}
    Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800

    $oldExe = '{old}'
    $newExeTemp = '{new_}'
    $appDir = '{app_dir}'

    Move-Item -Path $newExeTemp -Destination $oldExe -Force

    $vbs = '{vbs_path}'
    if (Test-Path $vbs) {{
        $cmd = '""' + $oldExe + '""'
        $content = 'CreateObject(\"WScript.Shell\").Run \"' + $cmd + '\", 0, False' + "`n"
        [System.IO.File]::WriteAllText($vbs, $content, [System.Text.Encoding]::Default)
    }}

    Start-Process -FilePath $oldExe -WorkingDirectory $appDir
}} catch {{
    $_.Exception.Message + '`n' + $_.ScriptStackTrace | Out-File -FilePath '{error_log}' -Encoding utf8
}} finally {{
    Remove-Item -Path $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
"""
