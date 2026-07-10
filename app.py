"""말하는 그림 아바타 서버.

실행: .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import base64
import json
import queue
import subprocess
import threading
import uuid
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import AvatarPipeline, find_avatar_image

ROOT = Path(__file__).parent
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

DEFAULT_VOICE = "ko-KR-SunHiNeural"

app = FastAPI(title="말하는 그림 아바타")
jobs: dict[str, dict] = {}
work_q: "queue.Queue[str]" = queue.Queue()
pipeline = AvatarPipeline()


class SpeakReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    blink_interval: float = 4.0  # 평균 깜빡임 간격(초), 0 = 깜빡임 없음
    blink_strength: float = 1.0  # 0~1


class SpeakRtReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    engine: str = "neurosync"  # "neurosync" | "a2f"


def tts_to_wav(text: str, voice: str, wav: Path):
    mp3 = wav.with_suffix(".mp3")
    asyncio.run(edge_tts.Communicate(text, voice).save(str(mp3)))
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
                   check=True, capture_output=True)
    mp3.unlink()


def run_video_job(job_id: str, job: dict):
    """Phase A: 텍스트 → 립싱크 mp4."""
    job["status"] = "tts"
    wav = OUT / f"{job_id}.wav"
    tts_to_wav(job["req"].text, job["req"].voice, wav)

    job["status"] = "animating"
    mp4 = OUT / f"{job_id}.mp4"
    pipeline.generate(wav, mp4,
                      blink_interval=job["req"].blink_interval,
                      blink_strength=job["req"].blink_strength)
    job["status"] = "done"
    job["video_url"] = f"/media/{job_id}.mp4"


def run_rt_job(job_id: str, job: dict):
    """Phase B: 텍스트 → mp3 + 블렌드셰이프 프레임 (퍼펫 렌더러용)."""
    job["status"] = "tts"
    mp3 = OUT / f"{job_id}.mp3"
    asyncio.run(edge_tts.Communicate(job["req"].text, job["req"].voice).save(str(mp3)))
    wav = OUT / f"{job_id}.wav"
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
                   check=True, capture_output=True)

    job["status"] = "animating"
    if job["req"].engine == "a2f":
        import a2f_source  # lazy
        bs = a2f_source.audio_to_blendshapes(str(wav))
        # A2F 출력이 오디오보다 ~0.4s 늦음(prediction_delay 미적용) — 트랙을 당겨 A/V 싱크
        bs["frames"] = bs["frames"][int(0.4 * bs["fps"]):]
    else:
        import blendshape_source  # lazy: 모듈/모델은 첫 rt 요청 때 로드
        bs = blendshape_source.audio_to_blendshapes(str(wav))
    job["result"] = {"audio_url": f"/media/{job_id}.mp3", **bs}
    job["status"] = "done"


def worker():
    # ponytail: GPU가 직렬이라 워커 1개 큐로 충분
    while True:
        job_id = work_q.get()
        job = jobs[job_id]
        try:
            if job.get("kind") == "rt":
                run_rt_job(job_id, job)
            else:
                run_video_job(job_id, job)
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            wav_p = OUT / f"{job_id}.wav"
            if wav_p.exists():
                wav_p.unlink()


threading.Thread(target=worker, daemon=True).start()


@app.post("/api/speak")
def speak(req: SpeakReq):
    if not req.text.strip():
        raise HTTPException(400, "텍스트가 비어 있습니다.")
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "queued", "req": req}
    work_q.put(job_id)
    return {"job_id": job_id}


@app.post("/api/speak_rt")
def speak_rt(req: SpeakRtReq):
    if not req.text.strip():
        raise HTTPException(400, "텍스트가 비어 있습니다.")
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "queued", "req": req, "kind": "rt"}
    work_q.put(job_id)
    return {"job_id": job_id}


class CharacterCreateReq(BaseModel):
    name: str = "내 캐릭터"
    image_b64: str            # dataURL 또는 base64
    eye_l: list[float]        # [cx, cy, r] — 원본 이미지 픽셀 좌표
    eye_r: list[float]
    mouth_box: list[float]    # [x0, y0, x1, y1]
    mouth_center: list[float] # [cx, cy]


@app.post("/api/characters/create")
def create_character(req: CharacterCreateReq):
    from PIL import Image as PILImage

    from character_builder import build_character

    try:
        raw = base64.b64decode(req.image_b64.split(",")[-1])
    except Exception:
        raise HTTPException(400, "이미지 디코딩 실패")
    char_id = "u_" + uuid.uuid4().hex[:6]
    updir = ROOT / "uploads"
    updir.mkdir(exist_ok=True)
    src = updir / f"{char_id}.png"
    src.write_bytes(raw)

    with PILImage.open(src) as im:
        s = min(512 / im.width, 512 / im.height)
    width = max(12, round((req.mouth_box[2] - req.mouth_box[0]) * s * 0.42))
    eye_lw = max(3, round(req.eye_l[2] * s * 0.35))
    build_character(
        src, ROOT / "assets_characters" / char_id,
        name=req.name.strip() or "내 캐릭터",
        eyes={"L": tuple(req.eye_l), "R": tuple(req.eye_r)},
        mouth_box=tuple(req.mouth_box), mouth_center=tuple(req.mouth_center),
        mouth_style={"width": width}, jaw_drop=6, closed_eye=("#1a1a1a", eye_lw))
    return {"id": char_id}


@app.get("/api/characters")
def characters():
    out = []
    cdir = ROOT / "assets_characters"
    if cdir.exists():
        for d in sorted(cdir.iterdir()):
            if (d / "base.png").exists():
                name = d.name
                try:
                    name = json.loads((d / "manifest.json").read_text()).get("name", d.name)
                except Exception:
                    pass
                out.append({"id": d.name, "name": name})
    return out


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "없는 작업입니다.")
    return {k: v for k, v in job.items() if k != "req"}


@app.get("/api/health")
def health():
    img = find_avatar_image()
    return {
        "image": img.name if img else None,
        "joyvasa_ready": (ROOT / "JoyVASA").exists(),
        "a2f": (ROOT / "Audio2Face-3D-SDK").exists(),
    }


@app.get("/api/avatar")
def avatar_image():
    img = find_avatar_image()
    if img is None:
        raise HTTPException(404, "그림 파일이 없습니다.")
    return FileResponse(img)


@app.get("/")
def index():
    # 배포 환경(JoyVASA 없음)에서는 퍼펫 모드가 메인
    page = "index.html" if (ROOT / "JoyVASA").exists() else "puppet.html"
    return FileResponse(ROOT / "static" / page)


@app.get("/puppet")
def puppet():
    return FileResponse(ROOT / "static" / "puppet.html")


app.mount("/media", StaticFiles(directory=OUT), name="media")
app.mount("/characters", StaticFiles(directory=ROOT / "assets_characters"), name="characters")
