# -*- coding: utf-8 -*-
"""core 业务逻辑层：与 UI 工具包完全解耦（禁止 import tkinter / PySide6）。

- config             配置读写（脏值容错，键与 v2 完全一致）
- http_client        HTTP 基础（直连 → 系统代理回退 → 网络层失败重试）
- providers_zhipu    智谱 GLM Coding Plan（5h/周窗口、MCP 月额度、近 30/15/7 天用量）
- providers_volcano  火山引擎 Agent Plan（SigV4 签名）
- burn_predict       本地用量历史 + 最小二乘烧速外推 + 时间格式化
- notify             Windows 系统通知（Shell_NotifyIcon）+ 阈值预警状态机
- autostart          开机自启（启动目录 VBS）
- updater            应用内增量更新（GitHub Releases，无头版）

所有模块只依赖 Python 标准库，可在无图形环境下导入与测试。
"""

CORE_VERSION = "3.0.0"
