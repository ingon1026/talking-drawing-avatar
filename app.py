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
from contextlib import asynccontextmanager
from pathlib import Path

import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline import AvatarPipeline, can_animate

ROOT = Path(__file__).parent
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

DEFAULT_VOICE = "ko-KR-InJoonNeural"   # 남성 기본 — 클라이언트가 voice 를 안 보낼 때만 쓰인다


@asynccontextmanager
async def lifespan(app):
    # 서버가 실제로 뜰 때만 백그라운드 스레드를 띄운다. 예전엔 모듈 최상위에서 start() 해서
    # `import app` 만 해도(테스트·tools/) 워밍업 스레드가 돌았고, 그 스레드가 백그라운드로
    # numpy 를 끌어오는 사이 메인의 pytest.approx 가 반쯤 초기화된 numpy 를 잡아
    # test_emotion_api 가 들쭉날쭉 실패했다.
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=warmup, daemon=True).start()
    yield


app = FastAPI(title="말하는 그림 아바타", lifespan=lifespan)
jobs: dict[str, dict] = {}
work_q: "queue.Queue[str]" = queue.Queue()
pipeline = AvatarPipeline()


class SpeakReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    blink_interval: float = 4.0  # 평균 깜빡임 간격(초), 0 = 깜빡임 없음
    blink_strength: float = 1.0  # 0~1
    # 얼굴은 둘 중 하나로 **반드시** 지정된다 — 서버가 알아서 고르는 기본 얼굴은 없다.
    image_b64: str | None = None  # 업로드 사진(dataURL/base64)
    char_id: str | None = None    # 등록 캐릭터 id — 그 캐릭터의 source.png 로 영상 생성
    # 감정 → 목소리 톤 (비율, 0 = 평상시). 클라이언트의 AvatarCore.voiceProsody 산출값.
    # 톤이 바뀐 오디오를 JoyVASA 가 먹으므로 표정·머리 움직임도 그만큼 따라온다.
    rate: float = 0.0
    pitch: float = 0.0
    volume: float = 0.0
    emotion: str | None = None      # 표정(눈썹)용 감정 라벨
    intensity: float = 1.0
    # auto_emo: 감정을 서버가 판정한다. 그러면 판정(≈0.39s)을 TTS(≈0.56s)와 겹쳐 돌릴 수
    # 있다 — 클라이언트가 먼저 /api/emotion 을 치면 그 시간이 통째로 직렬로 붙는다.
    # prosody_table: {감정: {rate,pitch,volume}} 를 intensity 1 기준으로 받는다. 값 자체는
    # avatar_core.js VOICE_STYLE 이 단일 출처 — 파이썬에 다시 적으면 갈라진다.
    auto_emo: bool = False
    prosody_table: dict[str, dict[str, float]] | None = None


class SpeakRtReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    engine: str = "neurosync"  # "neurosync" | "a2f"
    # 감정 → 목소리 톤 (비율, 0 = 평상시). 클라이언트의 AvatarCore.voiceProsody 산출값.
    rate: float = 0.0
    pitch: float = 0.0
    volume: float = 0.0
    emotion: str | None = None      # 표정(눈썹)용 감정 라벨
    intensity: float = 1.0


MULTI_VOICE = "ko-KR-HyunsuMultilingualNeural"  # 영어/한영혼합용 — 한국어전용 음성은 영어를 뭉갬


def _english_heavy(text: str) -> bool:
    """영문 글자 수가 한글 음절 수보다 많으면 영어 위주 문장으로 본다."""
    en = sum(c.isascii() and c.isalpha() for c in text)
    ko = sum("가" <= c <= "힣" for c in text)
    return en > ko


def _resolve_voice(text: str, voice: str) -> str:
    """실제로 합성에 쓰일 음성. 한영혼합에서도 스왑되므로 호출측이 이걸 알아야 한다 —
    예전엔 tts_to_wav 안에서만 바꿔서, _base_f0 가 스왑된 음성의 오디오를 원래 음성 키로
    캐시할 수 있었다(그 뒤 한국어 요청 전부가 틀린 기준 F0 를 씀)."""
    return MULTI_VOICE if "Multilingual" not in voice and _english_heavy(text) else voice


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
    voice = _resolve_voice(text, voice)
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


_VOICE_F0: dict[str, float] = {}


def _base_f0(voice: str, wav: Path) -> float:
    """음성의 기본 F0(Hz). 첫 합성본에서 한 번만 재고 캐시한다.

    edge-tts 의 pitch 는 **비율이 아니라 절대 Hz 오프셋**(`+16Hz`)이라, 그걸 리샘플
    배율로 바꾸려면 기준 F0 가 필요하다. 음성별 상수표를 박으면 음성이 바뀔 때 조용히
    틀리므로 실제 오디오에서 뽑는다. pyin 은 162ms 지만 음성당 1회라 요청 비용은 0.

    ponytail: 그 음성으로 처음 합성한 **한 문장**으로 정하고 프로세스 내내 쓴다. 같은
    음성도 문장에 따라 중앙 F0 가 155~172Hz 로 11% 흔들려서 배율 q 에 그대로 들어간다.
    첫 문장이 짧거나 특이하면 그 값이 굳는다 — 거슬리면 처음 N 개를 평균 내거나 2초
    미만 샘플은 버리고 다시 재도록 고칠 것.
    """
    if voice not in _VOICE_F0:
        import librosa
        import numpy as np
        y, sr = librosa.load(str(wav), sr=16000)
        f0, _, _ = librosa.pyin(y, fmin=60, fmax=400, sr=sr)
        v = f0[np.isfinite(f0)]
        _VOICE_F0[voice] = float(np.median(v)) if len(v) else 160.0
    return _VOICE_F0[voice]


def _prosody_filter(p: dict, f0: float, sr: int = 16000) -> str:
    """SSML 프로소디와 같은 효과를 내는 ffmpeg 필터 체인 (없으면 빈 문자열).

    asetrate 로 피치를 올리면 속도까지 같이 변하므로 atempo 로 되돌리고 거기에 rate 를
    곱한다. 실측(surprise, 배율 1.142)에서 spectral centroid 비가 1.072 로 배율에 한참
    못 미쳐 포먼트 이동(chipmunk)은 관측되지 않았다 — Hz 오프셋이 ±6~22Hz 로 작아서다.
    """
    q = (f0 + 100 * p.get("pitch", 0.0)) / f0
    tempo = (1 + p.get("rate", 0.0)) / q
    chain = []
    if abs(q - 1) > 1e-3:
        chain += [f"asetrate={sr}*{q:.6f}", f"aresample={sr}"]
    if abs(tempo - 1) > 1e-3:
        chain.append(f"atempo={tempo:.6f}")
    vol = p.get("volume", 0.0)
    if abs(vol) > 1e-3:
        # SSML `+15%` 의 실측 RMS 배율은 1.138 — 나이브한 1.15 가 아니다. 그 한 점으로 선형화.
        chain.append(f"volume={1 + vol * 0.92:.4f}")
    return ",".join(chain)


def _apply_prosody(wav: Path, p: dict, voice: str):
    """이미 만들어진 중립 wav 에 프로소디를 입힌다 (제자리 교체, ~60ms)."""
    af = _prosody_filter(p, _base_f0(voice, wav))
    if not af:
        return
    tmp = wav.with_suffix(".pros.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-af", af,
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)], check=True)
    tmp.replace(wav)


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


def _char_video_ok(d: Path) -> bool:
    """캐릭터 폴더가 영상(JoyVASA) 경로를 탈 수 있는가. 목록과 /api/speak 가 같이 쓴다 —
    두 곳이 다른 규칙을 쓰면 클라이언트는 라디오를 열어 주고 서버는 막는 상태가 된다.

    manifest 의 "video" 가 정답이다(등록 때 can_animate 로 판정해 적는다). 키가 없으면
    예전 캐릭터라 source.png 존재 여부로 폴백한다 — 마이그레이션은 별도 담당.
    """
    try:
        mf = json.loads((d / "manifest.json").read_text())
    except Exception:
        return (d / "source.png").exists()
    # .get("video", 폴백) 이 아니라 키 유무로 본다 — 판정 결과가 false 인 캐릭터를
    # "키 없음" 과 뭉개면 source.png 가 있다는 이유로 폴백이 되살려 버린다.
    return bool(mf["video"]) if "video" in mf else (d / "source.png").exists()


def run_video_job(job_id: str, job: dict):
    """Phase A: 텍스트(+선택 업로드 사진) → 립싱크 mp4."""
    req = job["req"]
    job["status"] = "tts"
    wav = OUT / f"{job_id}.wav"
    emotion, intensity = req.emotion, req.intensity
    if req.auto_emo and not req.emotion:
        # 감정 판정과 TTS 를 동시에 돌린다. 직렬로 두면 판정(≈0.39s)이 끝나야 SSML
        # 프로소디를 정할 수 있어 통째로 대기 시간이다. 중립으로 합성해 두고 판정이
        # 오면 같은 값을 ffmpeg 로 입힌다 — 체감 대기가 그만큼 줄어든다.
        got = {}

        def classify():
            try:
                import llm_source
                s = llm_source.split_sentences(req.text)
                labs = llm_source.classify_cached(tuple(s)) if s else None
                if labs:
                    got["lab"] = labs[0]
            except Exception as e:
                # 판정 실패는 중립으로 진행한다 — 영상 자체는 나와야 한다.
                # ponytail: 예전 클라이언트는 여기서 규칙 폴백(inferEmotion)을 탔지만,
                # 실측 8문장 중 7문장이 기권(발화한 1문장만 정답)이라 사실상 중립과 같다.
                # 다만 조용히 넘기지는 않는다 — 전이 의존성(requests 등)이 빠지면 증상이
                # "감정이 항상 중립" 뿐이라 원인을 못 찾는다. _llm() 이 존재하는 이유와 같다.
                print(f"[video] 감정 판정 건너뜀 ({type(e).__name__}: {e}) — 중립으로 진행")

        th = threading.Thread(target=classify)
        th.start()
        tts_to_wav(req.text, req.voice, wav)      # 중립 프로소디로 즉시 합성
        th.join()
        lab = got.get("lab")
        if lab:
            emotion, intensity = lab["emo"], lab["intensity"]
            row = (req.prosody_table or {}).get(emotion) or {}
            if not row:
                # 감정은 판정됐는데 목소리만 평평한 영상이 나온다 — 에러도 안 나서
                # 무증상이다. 테이블은 클라이언트가 요청마다 실어 보내므로(단일 출처가
                # avatar_core.js 의 VOICE_STYLE) 여기서 빠지면 그쪽 변경을 의심한다.
                print(f"[video] prosody_table 에 '{emotion}' 없음 — 목소리는 중립으로 나갑니다")
            # 요청 음성이 아니라 **실제 합성에 쓰인** 음성으로 F0 를 잡아야 한다.
            _apply_prosody(wav, {k: v * intensity for k, v in row.items()},
                           _resolve_voice(req.text, req.voice))
        job["emotion"] = emotion    # 클라이언트가 표정 상태를 맞출 수 있게 알려준다
    else:
        # 감정을 이미 안다(수동 버튼 / 감정 자동 꺼짐) — 겹칠 게 없으니 SSML 이 낫다.
        tts_to_wav(req.text, req.voice, wav,
                   prosody={"rate": req.rate, "pitch": req.pitch, "volume": req.volume})

    # 등록 캐릭터면 그 원본으로 애니메이션. base.png(눈·입 지운 것)가 아니라 source.png 다.
    img_path, do_crop = None, None
    if req.char_id:
        cand = ROOT / "assets_characters" / req.char_id / "source.png"
        if cand.exists():
            img_path, do_crop = cand, True
    if img_path is None and req.image_b64:
        updir = ROOT / "uploads"
        updir.mkdir(exist_ok=True)
        img_path = updir / f"{job_id}.png"
        img_path.write_bytes(base64.b64decode(req.image_b64.split(",")[-1]))
        do_crop = True

    job["status"] = "animating"
    mp4 = OUT / f"{job_id}.mp4"
    try:
        from pipeline import emotion_exp_delta
        pipeline.generate(wav, mp4, blink_interval=req.blink_interval,
                          blink_strength=req.blink_strength, image=img_path, do_crop=do_crop,
                          exp_delta=emotion_exp_delta(emotion, intensity))
    finally:
        # 업로드 임시본만 정리한다 — 캐릭터 source.png 는 영구 자산이라 건드리지 않는다.
        if img_path and img_path.exists() and img_path.parent.name == "uploads":
            img_path.unlink()
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


@app.post("/api/speak")
def speak(req: SpeakReq):
    if not req.text.strip():
        raise HTTPException(400, "텍스트가 비어 있습니다.")
    # 얼굴은 반드시 명시돼야 한다. 잡을 만들어 놓고 워커에서 실패시키면 사용자는
    # 4초 기다린 끝에 알게 되므로 여기서 즉시 막는다.
    if not req.char_id and not req.image_b64:
        raise HTTPException(400, "애니메이션할 사진이나 캐릭터를 먼저 지정해주세요.")
    # 얼굴 검출이 안 되는 그림(손그림 낙서 등)은 영상을 만들면 그림 전체가 얼굴로 잡혀
    # 통째로 늘었다 줄었다 한다. 클라이언트가 라디오를 잠그지만 API 를 직접 치면 뚫린다.
    # 워커가 아니라 여기서 막는 이유는 위 가드와 같다 — 잡을 만들면 사용자는 몇 초
    # 기다린 끝에 "error" 만 보게 된다. 없는 char_id 는 건드리지 않는다(예전부터
    # image_b64 폴백으로 넘어간다 — 그건 이 변경의 범위가 아니다).
    if req.char_id:
        d = ROOT / "assets_characters" / req.char_id
        if d.exists() and not _char_video_ok(d):
            raise HTTPException(400, "이 캐릭터는 얼굴 검출이 안 돼 영상을 만들 수 없습니다. "
                                     "실시간 모드로 말하게 해주세요.")
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


def _llm():
    """llm_source 지연 임포트(모듈·모델은 첫 사용 때 로드) — 없으면 원인을 구분해 503.

    ModuleNotFoundError.name 이 "llm_source" 여야 모듈 자체가 없는 것이다. requests 같은
    전이 의존성이 빠진 경우는 이름이 다르게 잡히므로, 같은 메시지로 뭉개면 "llm_source.py
    없음"이라고 오보하게 된다 — /api/chat 만 이 구분을 하고 /api/emotion 은 놓쳤었다.
    """
    try:
        import llm_source
        return llm_source
    except ModuleNotFoundError as e:
        if e.name == "llm_source":
            raise HTTPException(503, "llm_source.py 가 없습니다.")
        raise HTTPException(503, f"llm_source 의존성 누락: {e}")   # requests 등


class ChatReq(BaseModel):
    text: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": str}, ...] 최근 턴
    persona: str | None = None  # 선택된 캐릭터 성격(manifest.persona), 없으면 기본 정체성


@app.post("/api/chat")
def chat(req: ChatReq):
    """사용자 발화 → LLM 응답 {reply, emotion}. 발화(speak_rt)는 클라이언트가 이어서 호출."""
    if not req.text.strip():
        raise HTTPException(400, "빈 입력입니다.")
    llm_source = _llm()
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
    llm_source = _llm()
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
    # build_character 가 파일 경로를 받으므로 잠깐 떨군다. 끝나면 지운다 — 예전엔
    # 남겨 둬서 캐릭터마다 원본이 source.png 와 uploads 에 두 벌씩 쌓였다(실측 85MB).
    # finally 인 이유: 빌드가 실패해도 지워야 한다. 고아 17개가 그렇게 생긴 걸로 보인다.
    src = updir / f"{char_id}.png"
    src.write_bytes(raw)
    cdir = ROOT / "assets_characters" / char_id
    try:
        with PILImage.open(src) as im:
            s = min(512 / im.width, 512 / im.height)
        width = max(12, round((req.mouth_box[2] - req.mouth_box[0]) * s * 0.42))
        eye_lw = max(3, round(req.eye_l[2] * s * 0.35))
        build_character(
            src, cdir,
            name=req.name.strip() or "내 캐릭터",
            eyes={"L": tuple(req.eye_l), "R": tuple(req.eye_r)},
            mouth_box=tuple(req.mouth_box), mouth_center=tuple(req.mouth_center),
            mouth_style={"width": width}, jaw_drop=6, closed_eye=(None, eye_lw),
            deletable=True)
        # 원본을 캐릭터 자산으로 보관 — JoyVASA(영상 생성)는 눈·입이 지워진 base 가 아니라
        # 손대지 않은 그림이 있어야 한다. 캐릭터를 지울 때 같이 지워지도록 폴더 안에 둔다.
        shutil.copyfile(src, cdir / "source.png")
    finally:
        src.unlink(missing_ok=True)
    # 영상 가능 여부를 manifest 에 박는다. source.png 존재 여부로 판단하면 "영상 불가"를
    # 표현하려고 사용자 원본을 지워야 하는데 복구가 안 된다 — 원본은 무조건 남기고
    # 판정은 명시적으로 적는다. build_character 가 쓴 뒤라 읽어서 키만 더한다.
    video = can_animate(cdir / "source.png")
    mf = json.loads((cdir / "manifest.json").read_text())
    mf["video"] = video
    (cdir / "manifest.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2))
    return {"id": char_id, "video": video}


class CanAnimateReq(BaseModel):
    image_b64: str            # dataURL 또는 base64


@app.post("/api/can-animate")
def can_animate_api(req: CanAnimateReq):
    """등록하지 않는 임시 사진도 미리 판정해 준다 — 영상 라디오를 눌러 놓고 깨진 결과를
    받는 대신, 고르는 자리에서 알려주려는 것."""
    if not req.image_b64.strip():
        raise HTTPException(400, "이미지가 비어 있습니다.")
    updir = ROOT / "uploads"
    updir.mkdir(exist_ok=True)
    tmp = updir / f"probe_{uuid.uuid4().hex[:12]}.png"
    try:
        # 디코딩 실패는 400 — create_character 와 같게 맞춘다. 여기서 video:false 로
        # 뭉개면 클라이언트가 "얼굴 검출 실패"라고 안내해 원인을 오보한다.
        tmp.write_bytes(base64.b64decode(req.image_b64.split(",")[-1]))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "이미지 디코딩 실패")
    try:
        return {"video": can_animate(tmp)}
    finally:
        tmp.unlink(missing_ok=True)


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
    shutil.rmtree(d)   # 원본(source.png)도 이 안에 있다 — uploads 엔 더 이상 안 남긴다
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
                            "deletable": bool(mf.get("deletable", False)),
                            # 영상 경로를 쓸 수 있는지 — 스프라이트 워프로는 입이 실제로
                            # 벌어지지 않는다(구강 픽셀이 원본에 없다).
                            "video": _char_video_ok(d)})
    return out


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "없는 작업입니다.")
    return {k: v for k, v in job.items() if k != "req"}


@app.get("/api/stream/{job_id}")
def stream_video(job_id: str):
    """완성 전부터 재생할 수 있게 mp4 를 자라는 대로 흘려보낸다.

    pipeline 이 프래그먼트 mp4(moov 가 맨 앞)로 쓰므로 브라우저는 파일 앞부분만 받아도
    재생을 시작할 수 있다. 다 만들고 트는 것보다 체감 대기가 3.1초 → 1초대로 줄어든다.
    """
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "없는 작업입니다.")
    path = OUT / f"{job_id}.mp4"

    def tail():
        import time
        deadline = time.time() + 180
        while not path.exists():
            if job["status"] == "error" or time.time() > deadline:
                return
            time.sleep(0.05)
        with path.open("rb") as f:
            while True:
                b = f.read(65536)
                if b:
                    yield b
                elif job["status"] in ("done", "error"):
                    # 완료 직전에 EOF 를 봤을 수 있다 — 남은 꼬리를 한 번 더 비운다.
                    while (b := f.read(65536)):
                        yield b
                    return
                elif time.time() > deadline:
                    return
                else:
                    time.sleep(0.05)

    return StreamingResponse(tail(), media_type="video/mp4")


@app.get("/api/health")
def health():
    # image 필드는 뺐다 — 서버에 "현재 아바타" 라는 개념이 더 이상 없다. 얼굴은 요청마다
    # char_id 나 image_b64 로 명시된다(예전엔 리포 최상위를 훑어 첫 이미지를 썼고,
    # 테스트 픽스처 test_1.png 가 잡혀 "/" 영상이 전부 돼지였다).
    return {
        "joyvasa_ready": (ROOT / "JoyVASA").exists(),
        "a2f": (ROOT / "Audio2Face-3D-SDK").exists(),
        "chat": (ROOT / "llm_source.py").exists(),
    }


@app.get("/")
@app.get("/puppet")
def puppet():
    # 페이지는 하나다. 예전엔 "/" 가 static/index.html(사진 → 영상 전용)을 서빙했는데,
    # 영상 경로가 퍼펫으로 합쳐지면서 열등한 중복이 됐다 — 감정도 캐릭터 선택도 없고,
    # 저장·스트리밍 같은 걸 매번 두 벌 고쳐야 했다. 사진 한 장 쓰는 기능은
    # 드롭다운의 "임시 사진" 항목으로 옮겼다.
    return FileResponse(ROOT / "static" / "puppet.html")


@app.get("/3d")
def studio3d():
    # 3D 스튜디오는 /puppet 으로 흡수됐다 — 기존 링크·북마크가 죽지 않게 딥링크로 넘긴다
    return RedirectResponse("/puppet?char=__mark3d__")


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
