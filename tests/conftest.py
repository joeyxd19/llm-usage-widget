# -*- coding: utf-8 -*-
"""pytest 公共配置：把 src/ 与 scripts/ 挂进 sys.path。

单测只覆盖纯逻辑（core/* 与 scripts/generate_manifest.py），不 import
任何 Qt 模块——qt/* 顶层 import PySide6，留给 CI 在装好依赖后跑
compileall 做语法级校验。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
