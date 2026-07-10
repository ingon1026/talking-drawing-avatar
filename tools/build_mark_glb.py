"""NVIDIA mark 헤드 → 모프타깃 GLB (Three.js용).

입력: bs_skin.npz(중립+52 델타) + mark_topo.npz(삼각형, parse_mark_topology.py 산출)
출력: static/assets3d/mark.glb — POSITION/NORMAL/indices + 52 morph targets (ARKIT_NAMES 순서)

사용: .venv/bin/python tools/build_mark_glb.py <mark_topo.npz>
"""
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from blendshape_source import ARKIT_NAMES

BS_NPZ = ROOT / "Audio2Face-3D-SDK/_data/audio2face-models/audio2face-3d-v2.3-mark/bs_skin.npz"
OUT = ROOT / "static" / "assets3d" / "mark.glb"


def vertex_normals(verts, tris):
    n = np.zeros_like(verts)
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    for i in range(3):
        np.add.at(n, tris[:, i], fn)
    l = np.linalg.norm(n, axis=1, keepdims=True)
    return (n / np.maximum(l, 1e-12)).astype(np.float32)


def main(topo_path):
    bs = np.load(BS_NPZ, allow_pickle=True)
    topo = np.load(topo_path)
    verts = bs["neutral"].astype(np.float32)
    tris = topo["tris"].astype(np.uint32)
    normals = vertex_normals(verts.astype(np.float64), tris)

    # npz 키는 camelCase(첫 글자 소문자)
    def key(n):
        return n[0].lower() + n[1:]

    morphs = [np.ascontiguousarray(bs[key(n)], dtype=np.float32) for n in ARKIT_NAMES]

    # 바이너리 버퍼 조립 (4바이트 정렬)
    blobs, views, accessors = [], [], []
    offset = 0

    def add(arr, target=None):
        nonlocal offset
        raw = arr.tobytes()
        pad = (-len(raw)) % 4
        blobs.append(raw + b"\0" * pad)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target:
            view["target"] = target
        views.append(view)
        offset += len(raw) + pad
        return len(views) - 1

    def acc(view, comp, count, atype, arr=None):
        a = {"bufferView": view, "componentType": comp, "count": count, "type": atype}
        if arr is not None:
            a["min"] = [float(x) for x in arr.min(0)] if atype == "VEC3" else [float(arr.min())]
            a["max"] = [float(x) for x in arr.max(0)] if atype == "VEC3" else [float(arr.max())]
        accessors.append(a)
        return len(accessors) - 1

    ARRAY_BUFFER, ELEMENT = 34962, 34963
    FLOAT, UINT = 5126, 5125

    pos_acc = acc(add(verts, ARRAY_BUFFER), FLOAT, len(verts), "VEC3", verts)
    nrm_acc = acc(add(normals, ARRAY_BUFFER), FLOAT, len(normals), "VEC3")
    idx = np.ascontiguousarray(tris.reshape(-1))
    idx_acc = acc(add(idx, ELEMENT), UINT, len(idx), "SCALAR")
    morph_accs = [acc(add(m, ARRAY_BUFFER), FLOAT, len(m), "VEC3", m) for m in morphs]

    import json
    gltf = {
        "asset": {"version": "2.0", "generator": "build_mark_glb"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "mark"}],
        "meshes": [{
            "name": "mark_head",
            "primitives": [{
                "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                "indices": idx_acc,
                "targets": [{"POSITION": a} for a in morph_accs],
                "material": 0,
            }],
            "extras": {"targetNames": list(ARKIT_NAMES)},
        }],
        "materials": [{
            "name": "skin",
            "pbrMetallicRoughness": {"baseColorFactor": [0.85, 0.72, 0.65, 1.0],
                                     "metallicFactor": 0.0, "roughnessFactor": 0.65},
        }],
        "buffers": [{"byteLength": offset}],
        "bufferViews": views,
        "accessors": accessors,
    }

    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    bin_chunk = b"".join(blobs)
    total = 12 + 8 + len(js) + 8 + len(bin_chunk)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(js), b"JSON") + js)
        f.write(struct.pack("<I4s", len(bin_chunk), b"BIN\0") + bin_chunk)
    print(f"OK: {OUT} ({total / 1e6:.1f}MB, verts={len(verts)}, tris={len(tris)}, morphs={len(morphs)})")


if __name__ == "__main__":
    main(sys.argv[1])
