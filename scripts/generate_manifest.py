# -*- coding: utf-8 -*-
"""生成 onedir 产物的 manifest.json。

遍历指定目录下所有文件，计算 sha256 和文件大小，输出 JSON。
previous 记录上一个版本号，客户端据此判断能否安全使用增量包。

封签模式（--seal）：manifest 在打包流水线里先于 update.zip / delta.zip
生成，这两个压缩包的哈希由 --seal 在产物齐了之后补写进 manifest 的
packages 段；客户端下载后按它校验完整性（v3.2.0 起）。
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


def seal_package_hashes(manifest_path, files):
    """把打包产物的 sha256 补写进 manifest 的 packages 段（就地写回）。

    键取文件 basename（客户端按下载 URL 的文件名查表）。文件不存在
    或读不了会直接抛异常——封签是发布闸门，缺产物就该让流水线失败。
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    packages = manifest.get("packages")
    if not isinstance(packages, dict):
        packages = {}
    for path in files:
        packages[os.path.basename(path)] = sha256_file(path)
    manifest["packages"] = packages
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest


if __name__ == "__main__":
    # --seal 封签模式：manifest 路径 + 待封签产物列表（update.zip / delta.zip）
    if len(sys.argv) >= 2 and sys.argv[1] == "--seal":
        if len(sys.argv) < 4:
            print(
                "Usage: python generate_manifest.py --seal <manifest.json>"
                " <file> [file...]",
                file=sys.stderr,
            )
            sys.exit(1)
        seal_package_hashes(sys.argv[2], sys.argv[3:])
        print("sealed:", sys.argv[2])
        sys.exit(0)
    if len(sys.argv) < 3:
        print(
            "Usage: python generate_manifest.py <base_dir> <version> [previous]\n"
            "       python generate_manifest.py --seal <manifest.json>"
            " <file> [file...]",
            file=sys.stderr,
        )
        sys.exit(1)
    previous = sys.argv[3] if len(sys.argv) > 3 else None
    manifest = generate_manifest(sys.argv[1], sys.argv[2], previous)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
