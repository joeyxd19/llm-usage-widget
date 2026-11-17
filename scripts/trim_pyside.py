# -*- coding: utf-8 -*-
"""PyInstaller 打包后裁剪 PySide6 运行时（构建层，不参与应用运行）。

用法：python scripts/trim_pyside.py dist/onedir/llm-usage-widget

src 只用 QtCore / QtGui / QtWidgets（图标全部 QPainter 现画，没有 QML、
没有 QTranslator、没有 SVG），所以 QML/Quick、翻译、metatypes 这些开发期
资料、开发工具 exe，以及 Sql/Test/DBus/PrintSupport/Xml/Network/OpenGL
等模块的 dll/pyd 都可以删掉。

裁剪原则是"只删确定用不到的"：
- release.yml 已经不再 --collect-all，本脚本只是兜底；
- plugins 下的 platforms / styles / imageformats / iconengines / generic
  一律保留（styles/qmodernwindowsstyle.dll 是窗口样式依赖）；
- 不动 Qt6Core/Qt6Gui/Qt6Widgets/QtSvg、icudtl.dat、msvcp/vcruntime；
- 不动 libcrypto-3.dll / libssl-3.dll —— Python 的 ssl（HTTPS 请求）要用；
- opengl32sw.dll（软件 OpenGL 回退，约 20 MB）会随 OpenGL\\w* 规则删除：
  只有 QOpenGLWidget / QtQuick 才需要它，本项目是纯 QWidget + QPainter
  光栅绘制，不创建 OpenGL 上下文；
- 裁完检查关键插件，缺了就从已安装的 PySide6 包里补回来（CI 里必然有）。
"""

import re
import shutil
import sys
from pathlib import Path

# 整个目录都删（只在 PySide6 目录下匹配，避免误伤应用自己的目录）
DROP_DIRS = {"qml", "metatypes", "typesystems", "include", "glue",
             "qmltypes", "doc", "scripts", "lib"}

# plugins 下保留的分类；其余（sqldrivers/tls/qmltooling/designer 等）整目录删
KEEP_PLUGIN_DIRS = {"platforms", "styles", "imageformats", "iconengines",
                    "generic"}

# Qt6Xxx.dll / Xxx.pyd 里可以删的模块
_DROP_RE = re.compile(
    r"^(?:lib)?(?:Qt6?)?"
    r"(?:Qml\w*|Quick\w*|Labs\w*|Designer\w*|Help|UiTools|Sql|Test|DBus|"
    r"PrintSupport|Xml\w*|Network|OpenGL\w*|Multimedia\w*|WebEngine\w*|"
    r"WebChannel|WebSockets|WebView\w*|Bluetooth|Nfc|Positioning\w*|"
    r"SerialPort|Sensors\w*|Charts\w*|DataVisualization|StateMachine\w*|"
    r"Scxml\w*|SpatialAudio|HttpServer|Graphs\w*|Pdf\w*|RemoteObjects\w*|"
    r"Concurrent|3D\w*|TextToSpeech|VirtualKeyboard|ShaderTools|"
    r"Core5Compat)"
    r"\.(?:dll|pyd)$",
    re.IGNORECASE,
)

# 开发期资料/调试符号，以及 CI 里 PostgreSQL 驱动带进来的库
_DROP_SUFFIXES = {".pyi", ".pdb", ".lib", ".exp", ".prl"}
_DROP_ROOT_NAMES = {"libpq.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll",
                    "libcrypto-1_1-x64.dll", "libssl-1_1-x64.dll"}

# 必须存在的插件：(分类, 文件名)
_REQUIRED_PLUGINS = [
    ("platforms", "qwindows.dll"),
    ("styles", "qmodernwindowsstyle.dll"),
]


def _is_pyside_path(path):
    return any(part.lower() == "pyside6" for part in path.parts)


def _stat(root):
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def _remove(path):
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
        return True
    except OSError as exc:
        print("  跳过（无法删除）%s：%s" % (path, exc))
        return False


def _trim(root):
    removed = 0

    # 1) PySide6 下的开发资料目录（先删深层，避免父目录删完再遍历）
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts),
                       reverse=True):
        if not path.is_dir() or not _is_pyside_path(path):
            continue
        if path.name.lower() in DROP_DIRS and _remove(path):
            removed += 1

    # 2) plugins 下用不到的插件分类（白名单之外的整个目录）
    for path in list(root.rglob("plugins")):
        if not (path.is_dir() and _is_pyside_path(path)):
            continue
        for item in list(path.iterdir()):
            if item.is_dir() and item.name.lower() not in KEEP_PLUGIN_DIRS:
                if _remove(item):
                    removed += 1

    # 3) 翻译：只留 qtbase_zh_CN.qm
    for path in list(root.rglob("translations")):
        if not (path.is_dir() and _is_pyside_path(path)):
            continue
        for item in list(path.iterdir()):
            if item.is_file() and item.name == "qtbase_zh_CN.qm":
                continue
            if _remove(item):
                removed += 1

    # 4) 开发工具 exe（qmlls/assistant/qmlformat/linguist/lupdate/rcc/uic…）
    for path in list(root.rglob("*.exe")):
        if path.is_file() and _is_pyside_path(path):
            if _remove(path):
                removed += 1

    # 5) 模块 dll/pyd、开发符号、PostgreSQL 残留库
    for path in list(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name.lower() in _DROP_ROOT_NAMES:
            if _remove(path):
                removed += 1
            continue
        if not _is_pyside_path(path):
            continue
        if path.suffix.lower() in _DROP_SUFFIXES:
            if _remove(path):
                removed += 1
            continue
        if _DROP_RE.match(name):
            if _remove(path):
                removed += 1
    return removed


def _pyside_dir(root):
    """定位产物里的 PySide6 目录（onedir 下通常是 _internal/PySide6）。"""
    for cand in (root / "_internal" / "PySide6", root / "PySide6"):
        if cand.is_dir():
            return cand
    for path in root.rglob("PySide6"):
        if path.is_dir() and (path / "QtCore.pyd").is_file():
            return path
    return None


def _plugins_dir(root):
    pyside = _pyside_dir(root)
    if pyside is None:
        return None
    plugins = pyside / "plugins"
    return plugins if plugins.is_dir() else None


def _restore_plugin(root, category, filename):
    """钩子没收到关键插件时，从已安装的 PySide6 包里补回来。"""
    try:
        import PySide6
        src = Path(PySide6.__file__).parent / "plugins" / category / filename
    except Exception:
        return None
    if not src.is_file():
        return None
    pyside = _pyside_dir(root)
    if pyside is None:
        return None
    dst = pyside / "plugins" / category / filename
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    return dst


def main():
    if len(sys.argv) < 2:
        print("用法：python scripts/trim_pyside.py <onedir 目录>")
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print("目录不存在：%s" % root)
        return 2

    before_files, before_bytes = _stat(root)
    removed = _trim(root)
    after_files, after_bytes = _stat(root)
    print("裁剪：删除 %d 项，文件 %d → %d（%.1f MB → %.1f MB）" % (
        removed, before_files, after_files,
        before_bytes / 1048576, after_bytes / 1048576))

    fatal = False
    base = _plugins_dir(root)
    for category, filename in _REQUIRED_PLUGINS:
        target = (base / category / filename) if base else None
        if target is not None and target.is_file():
            continue
        restored = _restore_plugin(root, category, filename)
        if restored:
            print("已补回缺失插件：%s" % restored)
            continue
        if category == "platforms":
            print("::error::缺少平台插件 %s，程序无法启动" % filename)
            fatal = True
        else:
            print("::warning::缺少样式插件 %s，将回退到默认样式" % filename)
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
