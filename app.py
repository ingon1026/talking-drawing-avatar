"""말하는 그림 아바타 서버.

실행: .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
"""
import asyncio
import base64
import json
import queue
import re
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

DEFAULT_VOICE = "ko-KR-InJoonNeural"   # 남성 기본 — 클라이언트가 voice 를 안 보낼 때만 쓰인다

app = FastAPI(title="말하는 그림 아바타")
jobs: dict[str, dict] = {}
work_q: "queue.Queue[str]" = queue.Queue()
pipeline = AvatarPipeline()


class SpeakReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    blink_interval: float = 4.0  # 평균 깜빡임 간격(초), 0 = 깜빡임 없음
    blink_strength: float = 1.0  # 0~1
    image_b64: str | None = None  # 업로드 사진(dataURL/base64). 없으면 폴더 기본 이미지


class SpeakRtReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    engine: str = "neurosync"  # "neurosync" | "a2f"
    # 감정 → 목소리 톤 (비율, 0 = 평상시). 클라이언트의 AvatarCore.voiceProsody 산출값.
    rate: float = 0.0
    pitch: float = 0.0
    volume: float = 0.0


MULTI_VOICE = "ko-KR-HyunsuMultilingualNeural"  # 영어/한영혼합용 — 한국어전용 음성은 영어를 뭉갬


def _english_heavy(text: str) -> bool:
    """영문 글자 수가 한글 음절 수보다 많으면 영어 위주 문장으로 본다."""
    en = sum(c.isascii() and c.isalpha() for c in text)
    ko = sum("가" <= c <= "힣" for c in text)
    return en > ko


async def _synth_mp3(text: str, voice: str, mp3: Path, kw: dict) -> list[dict]:
    """mp3 를 쓰면서 WordBoundary 마크를 모은다. offset 은 100ns 단위 → 초로 변환."""
    marks = []
    with open(mp3, "wb") as f:
        # boundary 기본값이 SentenceBoundary 라 명시하지 않으면 WordBoundary 청크가 아예 안 옴.
        async for chunk in edge_tts.Communicate(text, voice, boundary="WordBoundary", **kw).stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                marks.append({"offset": chunk["offset"] / 1e7, "text": chunk.get("text", "")})
    return marks


def tts_to_wav(text: str, voice: str, wav: Path, keep_mp3: bool = False, prosody=None) -> list[dict]:
    """텍스트 → wav(16k mono). 반환값은 단어 경계 마크 — 문장 시작 시각 계산에 쓴다."""
    # 영어 위주인데 한국어전용 음성을 골랐으면 멀티링구얼로 자동 스왑(립싱크는 언어 무관).
    if "Multilingual" not in voice and _english_heavy(text):
        voice = MULTI_VOICE
    mp3 = wav.with_suffix(".mp3")
    # edge-tts 는 rate/volume 은 퍼센트, pitch 는 Hz 문자열(부호 필수)을 받는다.
    p = prosody or {}
    kw = {"rate": f"{round(p.get('rate', 0) * 100):+d}%",
          "volume": f"{round(p.get('volume', 0) * 100):+d}%",
          "pitch": f"{round(p.get('pitch', 0) * 100):+d}Hz"}
    marks = asyncio.run(_synth_mp3(text, voice, mp3, kw))
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
    if not keep_mp3:
        mp3.unlink()
    return marks


_NONWORD = re.compile(r"[^\w]", re.UNICODE)


def sentence_starts(sentences: list[str], marks: list[dict]) -> list[float]:
    """각 문장의 시작 시각(초). 단어 마크를 글자 수만큼 순서대로 배분한다.

    edge-tts 는 문장 경계를 알려주지 않고 단어만 준다 — 문장의 (문장부호 제외) 글자 수를
    채울 때까지 마크를 소비하는 방식으로 경계를 복원한다.
    """
    starts, i = [], 0
    for s in sentences:
        if i >= len(marks):
            starts.append(starts[-1] if starts else 0.0)
            continue
        starts.append(marks[i]["offset"])
        need, got = len(_NONWORD.sub("", s)), 0
        while i < len(marks) and got < need:
            got += len(_NONWORD.sub("", marks[i]["text"]))
            i += 1
    return starts


def run_video_job(job_id: str, job: dict):
    """Phase A: 텍스트(+선택 업로드 사진) → 립싱크 mp4."""
    req = job["req"]
    job["status"] = "tts"
    wav = OUT / f"{job_id}.wav"
    tts_to_wav(req.text, req.voice, wav)

    # 업로드 사진이 있으면 그 사진으로 애니메이션(실사 → 얼굴 크롭 켬)
    img_path, do_crop = None, None
    if req.image_b64:
        updir = ROOT / "uploads"
        updir.mkdir(exist_ok=True)
        img_path = updir / f"{job_id}.png"
        img_path.write_bytes(base64.b64decode(req.image_b64.split(",")[-1]))
        do_crop = True

    job["status"] = "animating"
    mp4 = OUT / f"{job_id}.mp4"
    try:
        pipeline.generate(wav, mp4, blink_interval=req.blink_interval,
                          blink_strength=req.blink_strength, image=img_path, do_crop=do_crop)
    finally:
        if img_path and img_path.exists():
            img_path.unlink()   # 업로드 원본은 영상 만든 뒤 정리
    job["status"] = "done"
    job["video_url"] = f"/media/{job_id}.mp4"


def rt_result(r: SpeakRtReq, job_id: str) -> dict:
    """Phase B: 텍스트 → mp3 + 블렌드셰이프 프레임 (퍼펫 렌더러용). 0.6초급이라 동기 처리."""
    wav = OUT / f"{job_id}.wav"
    try:
        marks = tts_to_wav(r.text, r.voice, wav, keep_mp3=True,
                           prosody={"rate": r.rate, "pitch": r.pitch, "volume": r.volume})
        if r.engine == "a2f":
            import a2f_source as source  # lazy: 모듈/엔진은 첫 요청 때 로드
        else:
            import blendshape_source as source
        bs = source.audio_to_blendshapes(str(wav))
        out = {"audio_url": f"/media/{job_id}.mp3", **bs}
        # 문장 시작 시각 — 감정 전환용. llm_source 가 없거나 마크가 없으면 조용히 생략한다.
        try:
            import llm_source
            sentences = llm_source.split_sentences(r.text)
            if sentences and marks:
                out["sentences"] = [{"text": s, "start": t}
                                    for s, t in zip(sentences, sentence_starts(sentences, marks))]
        except Exception:
            pass
        return out
    finally:
        wav.unlink(missing_ok=True)


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


gpu_lock = threading.Lock()   # 영상 잡(워커)과 동기 rt 발화가 GPU를 겹쳐 쓰지 않게 직렬화


def worker():
    # ponytail: GPU가 직렬이라 워커 1개 큐로 충분 — rt 발화는 동기 엔드포인트로 이동, 여긴 영상 전용
    while True:
        job_id = work_q.get()
        job = jobs[job_id]
        purge_old_media()
        try:
            with gpu_lock:
                run_video_job(job_id, job)
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            wav_p = OUT / f"{job_id}.wav"
            if wav_p.exists():
                wav_p.unlink()


threading.Thread(target=worker, daemon=True).start()


def warmup():
    """기동 직후 경량 발화 엔진만 미리 로드 — 재시작 후 첫 발화 3.9s(neurosync)/2.4s(a2f) 제거.
    JoyVASA(영상, 콜드 26s+)는 제외: 영상 안 쓰는 세션까지 매 재기동마다 GPU를 태우게 된다."""
    import wave
    w = OUT / "warmup.wav"
    with wave.open(str(w), "w") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 4800)   # 0.3s 무음
    for mod in ("blendshape_source", "a2f_source"):
        try:
            with gpu_lock:
                __import__(mod).audio_to_blendshapes(str(w))
        except Exception:
            pass   # 엔진 하나가 없거나 실패해도 서버는 정상 기동
    w.unlink(missing_ok=True)


threading.Thread(target=warmup, daemon=True).start()


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
    """0.6초급 작업이라 잡큐+폴링(발화당 +0.2~0.35s 낭비) 대신 동기 응답."""
    if not req.text.strip():
        raise HTTPException(400, "텍스트가 비어 있습니다.")
    purge_old_media()
    with gpu_lock:   # 영상 생성 중이면 끝날 때까지 대기 (기존 큐 대기와 동일한 순서 보장)
        try:
            return rt_result(req, uuid.uuid4().hex[:12])
        except Exception as e:
            raise HTTPException(500, str(e))


class ChatReq(BaseModel):
    text: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": str}, ...] 최근 턴
    persona: str | None = None  # 선택된 캐릭터 성격(manifest.persona), 없으면 기본 정체성


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
        return llm_source.chat(req.text, req.history, req.persona)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


class EmotionReq(BaseModel):
    text: str


@app.post("/api/emotion")
def emotion(req: EmotionReq):
    """텍스트 → 문장별 감정. 실패는 503 — 클라이언트가 규칙 폴백으로 조용히 진행한다."""
    if not req.text.strip():
        raise HTTPException(400, "빈 입력입니다.")
    try:
        import llm_source
    except ModuleNotFoundError:
        raise HTTPException(503, "감정 분류를 쓸 수 없습니다 (llm_source.py 없음).")
    sentences = llm_source.split_sentences(req.text)
    if not sentences:
        raise HTTPException(400, "문장을 찾지 못했습니다.")
    try:
        labels = llm_source.classify_cached(tuple(sentences))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"segments": [{"text": s, **lab} for s, lab in zip(sentences, labels)]}


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


@app.get("/head")
def head_mirror():
    return FileResponse(ROOT / "static" / "head.html")


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
