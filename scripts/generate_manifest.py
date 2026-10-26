# -*- coding: utf-8 -*-
"""生成 onedir 产物的 manifest.json。

遍历指定目录下所有文件，计算 sha256 和文件大小，输出 JSON。
previous 记录上一个版本号，客户端据此判断能否安全使用增量包。
"""
import hashlib
import json
import os
import sys


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def generate_manifest(base_dir, version, previous=None):
    base_dir = os.path.abspath(base_dir)
    files = []
    for root, _dirs, filenames in os.walk(base_dir):
        for name in filenames:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base_dir).replace(os.sep, "/")
            files.append(
                {
                    "path": rel,
                    "size": os.path.getsize(full),
                    "sha256": sha256_file(full),
                }
            )
    files.sort(key=lambda x: x["path"])
    return {
        "version": version,
        "previous": previous,
        "file_count": len(files),
        "files": files,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage: python generate_manifest.py <base_dir> <version> [previous]",
            file=sys.stderr,
        )
        sys.exit(1)
    previous = sys.argv[3] if len(sys.argv) > 3 else None
    manifest = generate_manifest(sys.argv[1], sys.argv[2], previous)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
