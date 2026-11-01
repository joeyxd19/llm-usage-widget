# -*- coding: utf-8 -*-
"""生成 winget 社区源（microsoft/winget-pkgs）的三件套 manifest。

用法（发布新版后）：
    python scripts/generate_winget_manifest.py v3.2.0 \
        [--installer dist/installer/llm-usage-widget_v3.2.0_setup.exe] \
        [--out-dir winget]

- 版本号带不带 v 前缀均可
- InstallerSha256 优先对 --installer 指向的本地安装包现算；不传则自动找
  dist/installer/llm-usage-widget_v<版本>_setup.exe；本地也没有时必须用
  --sha256 显式给出（可对 Release 附件下载后计算）
- 输出目录结构与 winget-pkgs 仓库一致：
    <out>/j/JoeyXD.LLMUsageWidget/<版本>/JoeyXD.LLMUsageWidget*.yaml
- 提交流程见 README「提交到 winget 社区源」

纯标准库实现：YAML 手工拼（字段全部固定，不需要 yaml 依赖）。"""

import argparse
import hashlib
import os
import sys

REPO = "joeyxd19/llm-usage-widget"
PACKAGE_ID = "JoeyXD.LLMUsageWidget"
PUBLISHER = "joeyxd19"
PACKAGE_NAME = "LLM Usage Widget"
MANIFEST_VERSION = "1.6.0"
RELEASE_URL_FMT = (
    "https://github.com/%s/releases/download/v%%s/llm-usage-widget_v%%s_setup.exe"
    % REPO
)

DESCRIPTION = """\
Windows 桌面常驻悬浮窗，实时显示智谱 GLM Coding Plan 与火山引擎 Agent Plan
的额度用量：5 小时 / 本周 / 本月窗口百分比、重置倒计时、MCP 分模型明细，
支持贴边收起、烧速预测、阈值系统通知与应用内自动更新（下载后 sha256 校验）。
"""

TAGS = ["quota", "llm", "zhipu", "volcengine", "widget", "windows"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def render_version(version):
    return """\
PackageIdentifier: %s
PackageVersion: %s
DefaultLocale: zh-CN
ManifestType: version
ManifestVersion: %s
""" % (PACKAGE_ID, version, MANIFEST_VERSION)


def render_installer(version, installer_url, installer_sha256):
    return """\
PackageIdentifier: %s
PackageVersion: %s
Installers:
  - Architecture: x64
    InstallerType: inno
    InstallerUrl: %s
    InstallerSha256: %s
    InstallerSwitches:
      Custom: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
    Scope: user
    UpgradeBehavior: install
ManifestType: installer
ManifestVersion: %s
""" % (PACKAGE_ID, version, installer_url, installer_sha256, MANIFEST_VERSION)


def render_locale(version):
    tags = "".join("  - %s\n" % t for t in TAGS)
    return """\
PackageIdentifier: %s
PackageVersion: %s
PackageLocale: zh-CN
Publisher: %s
PublisherUrl: https://github.com/%s
PublisherSupportUrl: https://github.com/%s/issues
PackageName: %s
PackageUrl: https://github.com/%s
License: Freeware
ShortDescription: 智谱 / 火山引擎额度用量悬浮窗
Description: |
%s
Tags:
%sManifestType: defaultLocale
ManifestVersion: %s
""" % (PACKAGE_ID, version, PUBLISHER, PUBLISHER, REPO, PACKAGE_NAME,
       REPO,
       "".join("  %s\n" % line for line in DESCRIPTION.strip().splitlines()),
       tags, MANIFEST_VERSION)


def find_local_installer(version):
    """优先取 dist/installer 下的同名安装包（本地已编译过的场景）。"""
    path = os.path.join("dist", "installer",
                        "llm-usage-widget_v%s_setup.exe" % version)
    return path if os.path.isfile(path) else None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="生成 winget-pkgs 三件套 manifest")
    ap.add_argument("version", help="版本号，如 v3.2.0 或 3.2.0")
    ap.add_argument("--installer", help="本地安装包路径（用于现算 sha256）")
    ap.add_argument("--sha256", help="直接指定 InstallerSha256")
    ap.add_argument("--out-dir", default="winget",
                    help="输出根目录（默认 winget/）")
    args = ap.parse_args(argv)

    version = args.version.lstrip("vV")
    if not version.replace(".", "").isdigit():
        ap.error("非法版本号：%r（需形如 3.2.0）" % args.version)

    installer_url = RELEASE_URL_FMT % (version, version)

    # InstallerSha256 三级来源：--sha256 > --installer > dist/installer 自动查找
    if args.sha256:
        sha = args.sha256.strip().lower()
    else:
        local = args.installer or find_local_installer(version)
        if not local:
            ap.error("本地找不到安装包，无法计算 InstallerSha256；"
                     "请用 --installer 指定路径或 --sha256 直接给出")
        sha = sha256_file(local)
        print("已计算安装包 sha256（%s）" % local)
    if len(sha) != 64:
        ap.error("InstallerSha256 需为 64 位十六进制")

    out_dir = os.path.join(args.out_dir, PACKAGE_ID[0].lower(),
                           PACKAGE_ID, version)
    os.makedirs(out_dir, exist_ok=True)
    files = {
        "%s.yaml" % PACKAGE_ID: render_version(version),
        "%s.installer.yaml" % PACKAGE_ID:
            render_installer(version, installer_url, sha),
        "%s.locale.zh-CN.yaml" % PACKAGE_ID: render_locale(version),
    }
    for name, content in files.items():
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print("已生成 %s" % path)

    print(
        "\n提交指引：fork microsoft/winget-pkgs → 把 %s 目录复制进仓库同路径 →\n"
        "winget-pkgs 仓库跑 `winget validate` 通过后提 PR。详见 README。"
        % out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
