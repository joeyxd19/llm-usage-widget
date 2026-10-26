# -*- coding: utf-8 -*-
"""根据新旧 manifest 生成增量更新包 delta.zip。

变更包括：新增、修改、删除。delta.zip 中只包含新增/修改的文件（保持相对路径），
同时生成一个 delta.json 说明删除列表和变更原因，便于客户端处理。
"""
import json
import os
import sys
import zipfile


def load_manifest(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def generate_delta(new_dir, new_manifest, old_manifest_path, out_zip, out_json):
    old_manifest = load_manifest(old_manifest_path) if os.path.exists(old_manifest_path) else None
    old_files = {f["path"]: f for f in (old_manifest.get("files", []) if old_manifest else [])}
    new_files = {f["path"]: f for f in new_manifest.get("files", [])}

    changed = []
    for path, info in new_files.items():
        old = old_files.get(path)
        if old is None or old.get("sha256") != info["sha256"]:
            changed.append(path)

    removed = [p for p in old_files if p not in new_files]

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in changed:
            src = os.path.join(new_dir, path.replace("/", os.sep))
            zf.write(src, path)

    delta_info = {
        "new_version": new_manifest.get("version"),
        "old_version": old_manifest.get("version") if old_manifest else None,
        "changed": changed,
        "removed": removed,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(delta_info, f, indent=2, ensure_ascii=False)

    print(f"delta: {len(changed)} changed, {len(removed)} removed -> {out_zip}")


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print(
            "Usage: python generate_delta.py <new_dir> <new_manifest.json> "
            "<old_manifest.json> <out_delta.zip> <out_delta.json>",
            file=sys.stderr,
        )
        sys.exit(1)
    generate_delta(sys.argv[1], load_manifest(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])
