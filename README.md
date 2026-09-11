# LLM 用量悬浮窗

Windows 桌面常驻悬浮窗，一眼掌握 **智谱 GLM Coding Plan** 与 **火山引擎 Agent Plan** 的额度用量。

## 直接下载使用（免安装）

> 适用于任何 Windows 10/11 电脑，无需安装 Python 或任何依赖。

1. 打开 [Releases 发布页](https://github.com/joeyxd19/llm-usage-widget/releases)，在最新版本的 Assets 中下载 EXE 附件（`llm-usage-widget_版本号.exe`，约 10 MB）
2. 双击运行。首次运行若弹出 Windows SmartScreen 蓝色提示，点「更多信息」→「仍要运行」
3. 首次运行会弹出设置窗口，填入密钥即可开始使用

EXE 由 PyInstaller 打包，内嵌完整 Python 运行环境；密钥只保存在本机 `config.json`，不经过任何第三方服务器。

## 功能特性

- **双供应商监控**：智谱（5 小时 / 本周 / 近 30·15·7 天 token 用量 / MCP 联网工具）+ 火山（5 小时 / 本周 / 本月）
- **详情面板**：大字百分比、进度条、重置倒计时与绝对时间、分模型用量明细
- **贴边自动收起**：拖到屏幕边缘滑入收起，只留一条微型进度条；鼠标悬停即展开完整面板，移开自动收回
- **配色主题**：深色 / 浅色 / 自动跟随背景明暗
- **额度预警**：绿（正常）/ 黄（偏高）/ 红（临界）三档变色，阈值可调
- **高分屏适配**：DPI 感知渲染 + 界面缩放（75%~130%）+ 透明度可调
- **轻量常驻**：单文件 Python（仅标准库，零第三方依赖）；开机自启、单实例保护；亦可打包为免安装 EXE

## 快速开始

**方式一：下载 Release 附件 EXE（推荐，无需安装 Python）**

见上文「直接下载使用」，或直接打开 [Releases 页面](https://github.com/joeyxd19/llm-usage-widget/releases)。

**方式二：运行源码**（需 Python 3.10+，自带 tkinter，无第三方依赖）

```bash
python src/usage_widget.pyw
```

**方式三：静默启动脚本**

双击 `scripts/启动.vbs`：优先启动 EXE，没有 EXE 时自动改用本机 Python 跑源码。

**打包 EXE**（PyInstaller）：

```bash
pyinstaller --onefile --windowed --name 额度悬浮窗 --distpath release --workpath build --specpath build src/usage_widget.pyw
```

首次运行会弹出设置窗口，填入密钥即可：

| 供应商 | 密钥 | 获取方式 |
|---|---|---|
| 智谱 | API Key | [智谱开放平台](https://open.bigmodel.cn/) → API Keys |
| 火山引擎 | AccessKey ID / Secret | [火山引擎控制台](https://console.volcengine.com/) → 右上角头像 → API 访问密钥（AKLT 开头，需 `ArkFullAccess` 或 `ark:GetAFPUsage` 只读权限） |

> 密钥只保存在本机 `config.json`（与 EXE / 源码同目录，已被 `.gitignore` 排除），软件不经过任何第三方服务器。

## 目录结构

```
llm-usage-widget/
├── src/
│   └── usage_widget.pyw    # 主程序（单文件，仅标准库）
├── docs/
│   ├── 使用说明.md          # 完整使用说明与常见问题
│   └── 版本日志.md          # 各版本变更记录
├── scripts/
│   └── 启动.vbs             # 静默启动脚本（EXE 优先）
└── release/                 # 本地 PyInstaller 打包产物（不入库，发布版见 Releases）
    └── 额度悬浮窗.exe
```

## 使用说明与常见问题

完整文档见 [docs/使用说明.md](docs/使用说明.md)，
涵盖贴边收起、设置项、密钥配置、白色背景看不清、AK/SK 权限、防火墙提示等。
