# LLM 用量悬浮窗

Windows 桌面常驻悬浮窗，一眼掌握 **智谱 GLM Coding Plan** 与 **火山引擎 Agent Plan** 的额度用量。

[![CI](https://github.com/joeyxd19/llm-usage-widget/actions/workflows/ci.yml/badge.svg)](https://github.com/joeyxd19/llm-usage-widget/actions/workflows/ci.yml)

v3 起界面从 tkinter 重构为 **PySide6（Qt6）**：真圆角半透明窗口、QSS 深浅主题、
原生系统托盘，业务逻辑拆分到 `src/core/`（纯标准库，可独立单测）。

## 直接下载使用

> 适用于任何 Windows 10/11 电脑，无需安装 Python 或任何依赖。

1. 打开 [Releases 发布页](https://github.com/joeyxd19/llm-usage-widget/releases)，在最新版本的 Assets 中下载 `llm-usage-widget_vX.X.X_setup.exe`（安装包）：双击运行安装向导，**安装位置可自选**（默认 `%LocalAppData%\LLM Usage Widget`，无需管理员权限）；安装完成后开始菜单会出现「额度悬浮窗」，以后可在悬浮窗上**右键 → 检查更新…**一键升级
2. 首次运行若弹出 Windows SmartScreen 蓝色提示，点「更多信息」→「仍要运行」
3. 首次运行会弹出设置窗口，填入密钥即可开始使用

EXE 由 PyInstaller 打包，内嵌完整 Python 运行环境；密钥经 **Windows DPAPI 加密**
后保存在本机 `%APPDATA%\LLM Usage Widget\config.json`（绑定当前 Windows 账号解密），
不经过任何第三方服务器。

**用户数据与安装目录分离**：`config.json` / `history.jsonl` / `error.log` 固定放在
`%APPDATA%\LLM Usage Widget`，换安装位置、重装、升级都不会丢配置。

## 功能特性

- **双供应商监控**：智谱（5 小时 / 本周 / 近 30·15·7 天 token 用量（可按需开关）/ MCP 每月额度）+ 火山（5 小时 / 本周 / 本月）
- **详情面板**：大字百分比、进度条、重置倒计时与绝对时间、分模型用量明细
- **密钥加密落盘**：API Key / Secret 用 Windows DPAPI 加密后写入配置（v3.2.0 起），旧明文配置自动迁移；设置页支持**导出 / 导入配置**（跨机器导入时解不开的密文自动置空并提示）
- **安全自动更新**：应用内检查 GitHub Releases，下载后按发布 manifest 的 sha256 **校验完整性**，安装前自动备份、失败自动**回滚**；支持增量更新包（delta），失败回退完整包
- **网络容错**：直连优先、系统代理回退；网络抖动与 429/503 按 **2/4/8 秒指数退避**重试，并尊重服务端 `Retry-After`（封顶 60 秒）
- **烧速预测（默认关闭）**：设置里打开后，本地记录用量历史（`history.jsonl`，保留 14 天），对 5 小时 / 本周窗口外推消耗速率，提前预警"按当前烧速撑不到重置"
- **系统通知**：跨阈值 / 5 小时窗口临近重置时弹 Windows 通知，每窗口期只提醒一次，可开关
- **贴边自动收起**：拖到屏幕边缘滑入收起，只留一条微型进度条；鼠标悬停即展开完整面板，移开自动收回；**显示器拔插后窗口自动移回屏幕内**
- **配色主题**：深色 / 浅色 / 自动跟随背景明暗；界面缩放（75%~130%）+ 透明度可调
- **额度预警**：绿（正常）/ 黄（偏高）/ 红（临界）三档变色，阈值可调
- **开机自启**：应用内设置统一管理（静默启动 VBS），安装包不再另设自启选项；卸载时可选保留或删除配置数据
- **日志轮转**：`error.log` 超过 1MB 自动轮转，不会无限膨胀
- **轻量常驻**：悬浮窗本体仅标准库 + PySide6；单实例保护、托盘一键找回

## 快速开始

**方式一：下载 Release 附件 EXE（推荐，无需安装 Python）**

见上文「直接下载使用」，或直接打开 [Releases 页面](https://github.com/joeyxd19/llm-usage-widget/releases)。

**方式二：运行源码**（需 Python 3.10+）

```bash
pip install PySide6-Essentials
python src/qt/app.py
```

**方式三：静默启动脚本**

双击 `scripts/启动.vbs`：优先启动 EXE，没有 EXE 时自动改用本机 Python 跑源码。

**测试与 CI**

```bash
pip install pytest
python -m pytest tests -q        # 纯逻辑单测（不依赖 Qt / DPAPI / 网络）
python -m compileall src -q      # 语法级检查
```

推送与 PR 由 GitHub Actions 自动跑同样两步（`.github/workflows/ci.yml`）。

**打包**（PyInstaller + Inno Setup，与 release.yml 一致）：

```bash
pip install -r requirements.txt

# 1. 打包 onedir（安装包内容源）；产物：dist/onedir/llm-usage-widget/
pyinstaller --noconsole --clean --collect-all PySide6 \
  --distpath dist/onedir --workpath build/onedir \
  --name llm-usage-widget src/qt/app.py

# 2. 编译安装包 Setup.exe（需 Inno Setup 6）
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=X.X.X scripts/setup.iss

# 3. 生成更新 manifest；发布前用 --seal 封签 update.zip / delta.zip 的校验和
python scripts/generate_manifest.py dist/onedir/llm-usage-widget X.X.X 上个版本号
```

首次运行会弹出设置窗口，填入密钥即可：

| 供应商 | 密钥 | 获取方式 |
|---|---|---|
| 智谱 | API Key | [智谱开放平台](https://open.bigmodel.cn/) → API Keys |
| 火山引擎 | AccessKey ID / Secret | [火山引擎控制台](https://console.volcengine.com/) → 右上角头像 → API 访问密钥（AKLT 开头，需 `ArkFullAccess` 或 `ark:GetAFPUsage` 只读权限） |

> 密钥经 DPAPI 加密后保存，绑定当前 Windows 账号；源码运行的数据文件在源码目录
> （`config.json` 已被 `.gitignore` 排除）。软件不经过任何第三方服务器。

## 更新与发布机制

- 打 `vX.Y.Z` tag 推送即触发 [release.yml](.github/workflows/release.yml)：PyInstaller 打包 → 生成 manifest（含全部文件 sha256）→ 尝试增量包 → 压缩 update.zip → Inno Setup 编译 Setup.exe → **封签更新包校验和** → 发布
- 客户端下载更新包后先按 manifest `packages` 段校验 sha256，不匹配直接报错重下，绝不把损坏的包交给安装脚本；PowerShell 安装脚本替换文件前先备份、失败自动回滚
- 代码签名已预留：配置 `WINDOWS_SIGN_CERT_PFX` / `WINDOWS_SIGN_CERT_PASSWORD` 两个仓库 Secret（OV 级证书或 Azure Trusted Signing）后自动签名，未配置则跳过

## 提交到 winget 社区源

发版后可把安装包提交到 [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs)，让用户能 `winget install JoeyXD.LLMUsageWidget`：

```bash
# 1. 用 Release 附件（或本地 dist/installer 的同名文件）生成三件套 manifest
python scripts/generate_winget_manifest.py v3.2.0 \
  --installer dist/installer/llm-usage-widget_v3.2.0_setup.exe

# 2. fork winget-pkgs，把生成的 winget/j/JoeyXD.LLMUsageWidget/3.2.0/ 目录
#    原样复制进 fork 仓库的相同路径，提交
# 3. 本地校验（可选）：winget validate --manifest winget/j/JoeyXD.LLMUsageWidget/3.2.0/
# 4. 向 microsoft/winget-pkgs 的 master 提 PR，标题格式：
#    Add version: JoeyXD.LLMUsageWidget version 3.2.0
```

后续新版本重复第 1、2、4 步即可（包 ID 不变，只加新版本目录）。

## 目录结构

```
llm-usage-widget/
├── src/
│   ├── qt/                 # 界面层（PySide6）
│   │   ├── app.py          # 入口：单实例、DPI、错误日志、装配
│   │   ├── main_widget.py  # 悬浮窗本体（拖动/贴边收起/刷新链/屏幕监听）
│   │   ├── detail_panel.py # 详情面板
│   │   ├── settings_dialog.py  # 设置窗口（五页，含导出/导入配置）
│   │   ├── tray.py         # 系统托盘
│   │   └── ...             # theme / toast / update_ui / win32
│   ├── core/               # 业务逻辑（纯标准库，可单测）
│   │   ├── config.py       # 配置读写（脏值容错、密钥加解密）
│   │   ├── secure_store.py # Windows DPAPI 加密
│   │   ├── http_client.py  # 直连/代理 + 指数退避重试
│   │   ├── providers_zhipu.py / providers_volcano.py
│   │   ├── burn_predict.py # 本地历史 + 烧速预测
│   │   ├── notify.py       # 系统通知
│   │   ├── autostart.py    # 开机自启 VBS
│   │   └── updater.py      # 应用内自动更新（校验和 + 备份回滚）
│   └── usage_widget.pyw    # v2 tkinter 旧版入口（停更保留，勿用于新部署）
├── tests/                  # 纯逻辑单测（pytest，不依赖 Qt）
├── docs/
│   ├── 使用说明.md          # 完整使用说明与常见问题
│   └── 版本日志.md          # 各版本变更记录
├── scripts/
│   ├── setup.iss           # Inno Setup 安装包脚本
│   ├── generate_manifest.py    # 更新 manifest 生成 / --seal 封签
│   ├── generate_delta.py   # 增量更新包生成
│   ├── generate_winget_manifest.py  # winget 社区源 manifest 生成
│   └── 启动.vbs            # 静默启动脚本（EXE 优先）
└── .github/workflows/      # ci.yml（单测）/ release.yml（打 tag 发版）
```

## 使用说明与常见问题

完整文档见 [docs/使用说明.md](docs/使用说明.md)，
涵盖贴边收起、设置项、密钥配置、白色背景看不清、AK/SK 权限、防火墙提示等。
