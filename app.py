"""말하는 그림 아바타 서버.

실행: .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import base64
import json
import queue
import shutil
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
    # 감정 → 목소리 톤 (비율, 0 = 평상시). 클라이언트의 AvatarCore.voiceProsody 산출값.
    rate: float = 0.0
    pitch: float = 0.0
    volume: float = 0.0


def tts_to_wav(text: str, voice: str, wav: Path, keep_mp3: bool = False, prosody=None):
    mp3 = wav.with_suffix(".mp3")
    # edge-tts 는 rate/volume 은 퍼센트, pitch 는 Hz 문자열(부호 필수)을 받는다.
    p = prosody or {}
    kw = {"rate": f"{round(p.get('rate', 0) * 100):+d}%",
          "volume": f"{round(p.get('volume', 0) * 100):+d}%",
          "pitch": f"{round(p.get('pitch', 0) * 100):+d}Hz"}
    asyncio.run(edge_tts.Communicate(text, voice, **kw).save(str(mp3)))
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
    if not keep_mp3:
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
    wav = OUT / f"{job_id}.wav"
    r = job["req"]
    tts_to_wav(r.text, r.voice, wav, keep_mp3=True,
               prosody={"rate": r.rate, "pitch": r.pitch, "volume": r.volume})

    job["status"] = "animating"
    if job["req"].engine == "a2f":
        import a2f_source as source  # lazy: 모듈/엔진은 첫 요청 때 로드
    else:
        import blendshape_source as source
    bs = source.audio_to_blendshapes(str(wav))
    job["result"] = {"audio_url": f"/media/{job_id}.mp3", **bs}
    job["status"] = "done"


_last_purge = 0.0


def purge_old_media(hours: int = 24):
    """상시 서비스라 생성 파일이 무한 누적되지 않게 오래된 미디어 정리 (demo_* 제외, 10분 스로틀)."""
    global _last_purge
    import time
    now = time.time()
    if now - _last_purge < 600:
        return
    _last_purge = now
    cutoff = now - hours * 3600
    for f in OUT.iterdir():
        if (f.suffix in (".mp3", ".mp4", ".wav") and not f.name.startswith("demo_")
                and f.stat().st_mtime < cutoff):
            f.unlink(missing_ok=True)


def worker():
    # ponytail: GPU가 직렬이라 워커 1개 큐로 충분
    while True:
        job_id = work_q.get()
        job = jobs[job_id]
        purge_old_media()
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


class ChatReq(BaseModel):
    text: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": str}, ...] 최근 턴


@app.post("/api/chat")
def chat(req: ChatReq):
    """사용자 발화 → LLM 응답 {reply, emotion}. 발화(speak_rt)는 클라이언트가 이어서 호출."""
    if not req.text.strip():
        raise HTTPException(400, "빈 입력입니다.")
    try:
        import llm_source  # lazy: 모듈·모델은 첫 대화 때 로드
    except ModuleNotFoundError as e:
        if e.name == "llm_source":
            raise HTTPException(503, "대화 기능이 아직 설치되지 않았습니다 (llm_source.py 없음).")
        raise HTTPException(503, f"대화 모듈 의존성 누락: {e}")   # requests 등
    # llm_source.chat 은 사용자용 한국어 메시지를 담아 RuntimeError 로 던진다 → 그대로 503 전달.
    # 그 외 예외(진짜 버그)는 삼키지 않고 500 으로 propagate.
    try:
        return llm_source.chat(req.text, req.history)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


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
        mouth_style={"width": width}, jaw_drop=6, closed_eye=("#1a1a1a", eye_lw),
        deletable=True)
    return {"id": char_id}


@app.delete("/api/characters/{char_id}")
def delete_character(char_id: str):
    d = ROOT / "assets_characters" / char_id
    if not d.exists():
        raise HTTPException(404, "없는 캐릭터입니다.")
    try:
        deletable = json.loads((d / "manifest.json").read_text()).get("deletable", False)
    except Exception:
        deletable = False
    if not deletable:
        raise HTTPException(403, "기본 제공 캐릭터는 삭제할 수 없습니다.")
    shutil.rmtree(d)
    up = ROOT / "uploads" / f"{char_id}.png"
    if up.exists():
        up.unlink()
    return {"ok": True}


@app.get("/api/characters")
def characters():
    out = []
    cdir = ROOT / "assets_characters"
    if cdir.exists():
        for d in sorted(cdir.iterdir()):
            if (d / "base.png").exists():
                try:
                    mf = json.loads((d / "manifest.json").read_text())
                except Exception:
                    mf = {}
                out.append({"id": d.name, "name": mf.get("name", d.name),
                            "deletable": bool(mf.get("deletable", False))})
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
        "chat": (ROOT / "llm_source.py").exists(),
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


@app.get("/3d")
def studio3d():
    return FileResponse(ROOT / "static" / "studio3d.html")


app.mount("/media", StaticFiles(directory=OUT), name="media")
app.mount("/characters", StaticFiles(directory=ROOT / "assets_characters"), name="characters")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

# avatar_core.js: static/ 원본과 docs/ 복사본 해시 비교 — 불일치·누락 시 경고만(기동 계속)
try:
    import hashlib
    _h = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ("static/avatar_core.js", "docs/avatar_core.js")}
    if len(set(_h.values())) > 1:
        print("⚠️  avatar_core.js static/·docs/ 사본 불일치 — `cp static/avatar_core.js docs/` 로 동기화 필요")
except OSError as e:
    print(f"⚠️  avatar_core.js 동기화 점검 건너뜀: {e}")
