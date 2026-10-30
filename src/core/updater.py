# -*- coding: utf-8 -*-
"""应用内自动更新器（无头版，业务逻辑与 v2 src/updater.py 相同）。

查询 GitHub Releases，下载对应资产，然后派生一个独立的 PowerShell 脚本
完成"等当前进程退出 → 替换文件 → 重启"的流程。Windows 下运行中的 exe
被系统锁定，无法原地覆盖，必须由外部进程代劳。

与 v2 的差异只在 UI 边界：原版直接操作 tkinter（messagebox、进度窗、
root.after），本版全部改为回调，由 UI 层（src/qt/update_ui.py）接住并
转发到 Qt 主线程。检查/下载/比对/生成升级脚本的逻辑逐行保留。

回调一览（都可能从工作线程被调用，UI 层自行切换线程）：
- on_progress(start_text) / on_progress(pct, None)   下载进度窗与百分比
- on_prompt(info)      发现新版：dict(tag/body/size_text/asset/url/
                       download/dismiss)，与 v2 气泡字段一致
- on_message(title, text)   原 messagebox.showinfo 的提示类信息
- on_error(title, msg)      原 messagebox.showerror 的错误（静默检查不回调）
- on_quit()                 更新器拉起安装脚本后请求退出应用
线程安全入口：cancel() 取消下载；busy 属性判断是否检查/下载中。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .autostart import AUTOSTART_VBS_NAME

GITHUB_API = (
    "https://api.github.com/repos/joeyxd19/llm-usage-widget/releases/latest"
)
APP_NAME = "额度悬浮窗"

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
    def __init__(self, current_version, on_prompt=None, on_message=None,
                 on_error=None, on_progress=None, on_quit=None):
        self.current_version = current_version
        self.on_prompt = on_prompt    # 发现新版（气泡/对话框由 UI 层决定）
        self.on_message = on_message  # (title, text) 提示类信息
        self.on_error = on_error      # (title, msg) 错误提示
        self.on_progress = on_progress  # (arg)  str=进度窗标题；int=百分比
        self.on_quit = on_quit        # 请求退出应用（安装脚本已拉起）
        self.pkg = package_type()
        self._busy = False
        self._busy_since = 0.0
        self._downloading = False     # 下载进行中：busy 不因超时失效
        self._cancelled = False

    # ---------------- UI 入口 ----------------

    @property
    def busy(self):
        """是否正在检查/下载中。检查阶段的挂起状态超时视为已失效。"""
        if not self._busy:
            return False
        if self._downloading:
            return True   # 正在下载，耗时久也不能放行第二次检查
        if time.time() - self._busy_since > 120:
            self._busy = False
            return False
        return True

    def cancel(self):
        """请求取消下载（原版由进度窗关闭按钮触发）。"""
        self._cancelled = True

    def check(self, silent=False):
        """启动检查更新线程。"""
        if not self.pkg:
            if not silent and self.on_message:
                self.on_message("检查更新", "源码运行模式不支持自动更新。")
            return
        if self.busy:
            if not silent and self.on_message:
                self.on_message("检查更新", "正在检查更新，请稍候…")
            return
        self._busy = True
        self._busy_since = time.time()
        self._cancelled = False
        threading.Thread(
            target=self._check_worker, args=(silent,), daemon=True
        ).start()

    def _check_worker(self, silent):
        try:
            tag, body, assets = self._fetch_latest()
        except Exception as exc:
            # 注意：不能在 after 回调里引用 except 变量，Python 3 离开
            # except 块后会删除它，回调执行时只会抛 NameError。
            err = str(exc)
            self._on_error("检查更新失败", err, silent)
            return

        new_ver = parse_version(tag)
        cur_ver = parse_version(self.current_version)
        if not version_gt(new_ver, cur_ver):
            self._busy = False
            if not silent and self.on_message:
                self.on_message(
                    "检查更新", f"当前已是最新版本 v{self.current_version}。"
                )
            return

        if self.pkg == "onefile":
            # 单文件版直接替换 exe，不涉及增量逻辑
            asset = next(
                (a for a in assets if a.get("name") == self._asset_name(tag)), None
            )
            if asset is None:
                self._busy = False
                self._on_error(
                    "未找到更新包",
                    f"Release {tag} 中没有 {self._asset_name(tag)}，请去 Releases 页面手动下载。",
                    silent,
                )
                return
            url = asset["browser_download_url"]
            size = asset.get("size", 0)
            self._ask_update(tag, body, url, size)
            return

        # 文件夹版：优先增量包，但仅当上一条 release 正好是当前版本时才安全
        manifest_asset = next(
            (a for a in assets if a.get("name") == self._manifest_name(tag)), None
        )
        delta_asset = next(
            (a for a in assets if a.get("name") == self._delta_name(tag)), None
        )
        update_asset = next(
            (a for a in assets if a.get("name") == self._update_name(tag)), None
        )

        manifest = None
        if manifest_asset is not None:
            try:
                manifest = self._download_manifest(
                    manifest_asset["browser_download_url"]
                )
            except Exception:
                manifest = None

        prev_version = str((manifest or {}).get("previous") or "")
        can_delta = bool(
            delta_asset
            and manifest
            and prev_version
            and prev_version == self.current_version
        )
        chosen = delta_asset if can_delta else update_asset

        if chosen is None:
            self._busy = False
            self._on_error(
                "未找到更新包",
                f"Release {tag} 缺少安装包，请去 Releases 页面手动下载。",
                silent,
            )
            return

        url = chosen["browser_download_url"]
        size = chosen.get("size", 0)
        # 本地文件可能被占用/无权限导致哈希失败；算不出增量信息就按完整包提示，
        # 异常绝不能逃出 worker 线程，否则 _busy 卡死、后续检查全部被拦
        delta_info = None
        if can_delta:
            try:
                delta_info = self._compute_delta(manifest)
            except Exception:
                delta_info = None
        self._ask_update(tag, body, url, size, manifest, delta_info)

    def _ask_update(self, tag, body, url, size, manifest=None, delta_info=None):
        try:
            mb = size / (1024 * 1024) if size else 0
            size_text = f"约 {mb:.1f} MB" if mb else "大小未知"
            notes = (body or "暂无更新说明。").strip()
            if len(notes) > 400:
                notes = notes[:400] + "…"

            using_delta = delta_info is not None and url.endswith("_delta.zip")
            extra = ""
            if using_delta and delta_info.get("changed_count"):
                extra = f"（增量：{delta_info['changed_count']} 个文件变更）"
            asset_name = self._delta_name(tag) if using_delta else self._update_name(tag)

            if self.on_prompt:
                self.on_prompt(
                    {
                        "current": self.current_version,
                        "tag": tag,
                        "body": notes,
                        "size_text": size_text + (f" {extra}" if extra else ""),
                        "asset": asset_name,
                        "url": url,
                        "download": lambda: self._download_and_apply(tag, url, manifest),
                        "dismiss": lambda: setattr(self, "_busy", False),
                    }
                )
                return

            # 没有气泡通道（如纯 CLI 场景）：把信息交 on_message，不擅自下载
            self._busy = False
            if self.on_message:
                self.on_message(
                    "检查更新",
                    f"发现新版本：v{self.current_version} → {tag}\n\n"
                    f"更新包：{asset_name}（{size_text}）{extra}\n\n"
                    f"更新说明：\n{notes}",
                )
        except Exception as exc:
            # 提示构建失败时也要把错误抛到界面上，不能静默复位了事
            self._busy = False
            err = str(exc)
            self._on_error("检查更新失败", err, False)

    def _download_and_apply(self, version, url, manifest=None):
        self._show_progress(f"正在下载 v{version} …")
        self._downloading = True
        self._cancelled = False
        threading.Thread(
            target=self._download_worker, args=(version, url, manifest), daemon=True
        ).start()

    def _show_progress(self, text):
        if self.on_progress:
            self.on_progress(text)

    def _set_progress(self, pct, text=None):
        if self.on_progress:
            self.on_progress(pct, text)

    def _on_error(self, title, msg, silent):
        self._busy = False
        self._downloading = False
        if not silent and self.on_error:
            self.on_error(title, msg)

    # ---------------- 网络 ----------------

    def _version_from_tag(self, tag):
        return tag[1:] if tag and tag.lower().startswith("v") else tag

    def _update_name(self, tag):
        return f"llm-usage-widget_v{self._version_from_tag(tag)}_update.zip"

    def _delta_name(self, tag):
        return f"llm-usage-widget_v{self._version_from_tag(tag)}_delta.zip"

    def _manifest_name(self, tag):
        return f"llm-usage-widget_v{self._version_from_tag(tag)}_manifest.json"

    def _asset_name(self, tag):
        if self.pkg in ("onedir", "installed"):
            return self._update_name(tag)
        return f"llm-usage-widget_v{self._version_from_tag(tag)}.exe"

    def _download_manifest(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _compute_delta(self, manifest):
        """对比本地文件与 manifest，返回变更统计。"""
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
        new_files = {f["path"]: f for f in manifest.get("files", [])}
        changed = []
        for path, info in new_files.items():
            local = os.path.join(app_dir, path.replace("/", os.sep))
            if not os.path.isfile(local):
                changed.append(path)
                continue
            h = hashlib.sha256()
            with open(local, "rb") as f:
                while True:
                    chunk = f.read(256 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            if h.hexdigest() != info.get("sha256"):
                changed.append(path)
        return {
            "changed_count": len(changed),
            "total_count": len(new_files),
        }

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

    def _download_worker(self, version, url, manifest=None):
        ext = ".zip" if self.pkg in ("onedir", "installed") else ".exe"
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
                            self._set_progress(pct)
            self._on_downloaded(version, dest, manifest)
        except Exception as exc:
            err = str(exc)
            self._on_error("下载失败", err, False)

    def _on_downloaded(self, version, dest, manifest=None):
        self._set_progress(100, "下载完成，准备安装…")
        self._downloading = False
        try:
            self._spawn_updater(version, dest, manifest)
        except Exception as exc:
            self._on_error("更新失败", f"无法启动安装程序：{exc}", False)
            return
        # 给安装脚本留时间启动，然后退出自身
        t = threading.Timer(0.8, self._quit_app)
        t.daemon = True
        t.start()

    def _quit_app(self):
        if self.on_quit:
            self.on_quit()
        # 进程级兜底（v2 同款语义）：优雅退出若卡死（事件循环已异常、
        # 信号没送出去），旧进程会一直锁住 exe，安装脚本替换文件会失败。
        # 优雅退出成功时进程先没了，这个定时器不会有机会触发。
        t = threading.Timer(3.0, os._exit, args=(0,))
        t.daemon = True
        t.start()

    # ---------------- 安装脚本 ----------------

    def _spawn_updater(self, version, dest, manifest=None):
        if os.name != "nt":
            raise RuntimeError("自动更新仅支持 Windows 打包版")

        exe = os.path.abspath(sys.executable)
        app_dir = os.path.dirname(exe)
        pid = os.getpid()

        manifest_path = None
        if manifest:
            manifest_path = os.path.join(
                tempfile.gettempdir(),
                "llm-usage-widget-update-manifest-%d.json" % pid,
            )
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)

        if self.pkg in ("onedir", "installed"):
            ps = self._build_ps_onedir(pid, app_dir, dest, manifest_path)
        else:
            ps = self._build_ps_onefile(pid, exe, dest)

        # 文件名带上 PID：避免同机多份残留脚本互相覆盖；脚本自身会自删
        ps_path = os.path.join(
            tempfile.gettempdir(), "llm-usage-widget-updater-%d.ps1" % pid
        )
        # 必须带 BOM（utf-8-sig）：Windows PowerShell 5.1 对无 BOM 脚本按
        # 系统 ANSI 代码页（中文系统为 GBK）解码，脚本里嵌入的中文注释与
        # 含中文的安装目录/临时目录路径会全部乱码，导致更新悄悄失败
        with open(ps_path, "w", encoding="utf-8-sig") as f:
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

    def _build_ps_onedir(self, pid, app_dir, zip_path, manifest_path=None):
        app = _ps_escape(app_dir)
        zip_ = _ps_escape(zip_path)
        temp_dir = _ps_escape(
            os.path.join(tempfile.gettempdir(), "llm-usage-widget-update")
        )
        manifest = _ps_escape(manifest_path) if manifest_path else ""
        exe_name = "llm-usage-widget.exe"
        new_exe = _ps_escape(os.path.join(app_dir, exe_name))
        vbs_path = _ps_escape(self._autostart_vbs_path())
        error_log = _ps_escape(
            os.path.join(tempfile.gettempdir(), "llm-usage-widget-update-error.log")
        )

        if manifest_path:
            manifest_block = f"""
    $manifestPath = '{manifest}'
    $manifest = Get-Content -Path $manifestPath -Raw | ConvertFrom-Json
    $allowed = @{{}}
    foreach ($f in $manifest.files) {{ $allowed[$f.path] = $true }}

    # 1) 复制更新包里的新增/变更文件
    $srcDir = Join-Path $tempDir 'llm-usage-widget'
    Get-ChildItem -Path $srcDir -Recurse -File | ForEach-Object {{
        $rel = $_.FullName.Substring($srcDir.Length + 1) -replace '\\\\', '/'
        if ($allowed.ContainsKey($rel)) {{
            $dest = Join-Path $appDir ($rel -replace '/', '\\')
            $destDir = Split-Path -Parent $dest
            if (-not (Test-Path $destDir)) {{
                New-Item -ItemType Directory -Path $destDir -Force | Out-Null
            }}
            Copy-Item -Path $_.FullName -Destination $dest -Force
        }}
    }}

    # 2) 删除 _internal 中新版已不存在的旧文件（不动根目录的卸载程序等）
    $internalDir = Join-Path $appDir '_internal'
    if (Test-Path $internalDir) {{
        Get-ChildItem -Path $internalDir -Recurse -File | ForEach-Object {{
            $rel = $_.FullName.Substring($appDir.Length + 1) -replace '\\\\', '/'
            if (-not $allowed.ContainsKey($rel)) {{
                Remove-Item -Path $_.FullName -Force
            }}
        }}
        Get-ChildItem -Path $internalDir -Recurse -Directory |
            Sort-Object {{ $_.FullName.Length }} -Descending | ForEach-Object {{
            if ((Get-ChildItem -Path $_.FullName -Force | Measure-Object).Count -eq 0) {{
                Remove-Item -Path $_.FullName -Force
            }}
        }}
    }}

    # 3) 清理旧版带版本号的 exe
    Get-ChildItem -Path $appDir -Filter 'llm-usage-widget_v*.exe' | Remove-Item -Force
"""
        else:
            manifest_block = f"""
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
"""

        # 自启 VBS 内容与 core.autostart.vbs_content 同一格式（升级后不失效）。
        # 注意 $oldExe 必须在单引号串外拼接，单引号里不会做变量展开
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
{manifest_block}

    $vbs = '{vbs_path}'
    $oldExe = Join-Path $appDir $exeName
    if (Test-Path $vbs) {{
        $cmd = '""' + $oldExe + '""'
        $content = 'CreateObject(\"WScript.Shell\").Run \"' + $cmd + '\", 0, False' + "`n"
        [System.IO.File]::WriteAllText($vbs, $content, [System.Text.Encoding]::Default)
    }}

    Remove-Item -Path $tempDir -Recurse -Force
    Remove-Item -Path $zip -Force
    if ('{manifest}' -ne '') {{ Remove-Item -Path '{manifest}' -Force -ErrorAction SilentlyContinue }}
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
