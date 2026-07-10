"""Maya ASCII(.ma)에서 메시 토폴로지 추출 → npz (verts, tris).

.ma의 polyFaces는 엣지 인덱스 기반: vt(정점) / ed(엣지 v0,v1,hard) / fc(f n e...)를
파싱한 뒤 엣지를 정점 시퀀스로 풀고 팬 삼각분할한다.

사용: .venv/bin/python tools/parse_mark_topology.py <mark.ma> <메시이름> <출력.npz>
"""
import re
import sys

import numpy as np

ATTR_RE = re.compile(r'"\.(vt|ed|fc)\[[0-9:]+\]"')
PREFIX_RE = re.compile(r'^.*?"\.(?:vt|ed|fc)\[[0-9:]+\]"(?:\s+-type\s+"polyFaces")?')
NUM_RE = re.compile(r"-?\d+\.?\d*(?:[eE][+-]?\d+)?")


def parse_mesh(path, mesh_name):
    in_node = False
    data = {"vt": [], "ed": [], "fc": []}
    cur = None
    with open(path, errors="ignore") as f:
        for line in f:
            if line.startswith("createNode"):
                if in_node:
                    break
                in_node = f'-n "{mesh_name}"' in line
                continue
            if not in_node:
                continue
            if line.lstrip().startswith("setAttr"):
                m = ATTR_RE.search(line)
                if not m:
                    cur = None
                    continue
                cur = m.group(1)
                line = PREFIX_RE.sub("", line)
            if cur is None:
                continue
            chunk = line.strip().rstrip(";")
            if cur == "fc":
                data["fc"] += chunk.split()
            else:
                data[cur] += NUM_RE.findall(chunk)
            if line.rstrip().endswith(";"):
                cur = None
    return data


def faces_from_tokens(tokens, edges):
    faces, i = [], 0
    while i < len(tokens):
        if tokens[i] == "f":
            n = int(tokens[i + 1])
            loop = []
            for k in range(n):
                e = int(tokens[i + 2 + k])
                loop.append(edges[e][0] if e >= 0 else edges[-e - 1][1])
            faces.append(loop)
            i += 2 + n
        else:
            i += 1
    return faces


def main(ma_path, mesh_name, out_path):
    d = parse_mesh(ma_path, mesh_name)
    verts = np.array([float(x) for x in d["vt"]], dtype=np.float64).reshape(-1, 3)
    edges = np.array([int(float(x)) for x in d["ed"]], dtype=np.int64).reshape(-1, 3)[:, :2]
    faces = faces_from_tokens(d["fc"], edges)
    tris = []
    for fc in faces:
        for k in range(1, len(fc) - 1):
            tris.append((fc[0], fc[k], fc[k + 1]))
    tris = np.array(tris, dtype=np.int32)
    print(f"verts={len(verts)}, edges={len(edges)}, faces={len(faces)}, tris={len(tris)}")
    assert len(tris) and tris.min() >= 0 and tris.max() < len(verts), "면 인덱스 범위 오류"
    np.savez_compressed(out_path, verts=verts.astype(np.float32), tris=tris)
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
