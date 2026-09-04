# LLM 用量悬浮窗

Windows 桌面常驻悬浮窗，一眼掌握 **智谱 GLM Coding Plan** 与 **火山引擎 Agent Plan** 的额度用量。

## 功能特性

- **双供应商监控**：智谱（5 小时 / 本周 / MCP 联网工具）+ 火山（5 小时 / 本周 / 本月）
- **详情面板**：大字百分比、进度条、重置倒计时与绝对时间、分模型用量明细
- **贴边自动收起**：拖到屏幕边缘滑入收起，只留一条微型进度条；鼠标悬停即展开完整面板，移开自动收回
- **配色主题**：深色 / 浅色 / 自动跟随背景明暗
- **额度预警**：绿（正常）/ 黄（偏高）/ 红（临界）三档变色，阈值可调
- **高分屏适配**：DPI 感知渲染 + 界面缩放（75%~130%）+ 透明度可调
- **轻量常驻**：单文件 Python（tkinter），无第三方运行时依赖；开机自启、单实例保护

## 快速开始

**方式一：直接运行源码**（需 Python 3.10+，自带 tkinter）

```bash
cd 智谱用量监控桌面应用/usage-widget
python usage_widget.pyw
```

**方式二：打包 EXE**（PyInstaller）

```bash
pyinstaller --onefile --windowed usage_widget.pyw
```

首次运行会弹出设置窗口，填入密钥即可：

| 供应商 | 密钥 | 获取方式 |
|---|---|---|
| 智谱 | API Key | [智谱开放平台](https://open.bigmodel.cn/) → API Keys |
| 火山引擎 | AccessKey ID / Secret | [火山引擎控制台](https://console.volcengine.com/) → 右上角头像 → API 访问密钥（AKLT 开头，需 `ArkFullAccess` 或 `ark:GetAFPUsage` 只读权限） |

> 密钥只保存在本机 `config.json`（已被 `.gitignore` 排除），软件不经过任何第三方服务器。

## 目录结构

```
智谱用量监控桌面应用/
└── usage-widget/
    ├── usage_widget.pyw   # 主程序（单文件）
    ├── 使用说明.md        # 完整使用说明
    └── 启动.vbs           # 静默启动脚本
额度悬浮窗/                  # EXE 分发目录（EXE 与使用说明，不入库）
版本备份/
└── 版本日志.md            # 版本历史（各版 EXE 留档，不入库）
```

## 使用说明与常见问题

完整文档见 [额度悬浮窗/使用说明.md](额度悬浮窗/使用说明.md)，
涵盖贴边收起、设置项、密钥配置、白色背景看不清、AK/SK 权限、防火墙提示等。
