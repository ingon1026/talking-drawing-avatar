/* avatar_core.js — 세 아바타 페이지(static/puppet.html, docs/index.html, static/studio3d.html)의
 * 공유 렌더 코어. 복붙 드리프트 방지를 위해 공통 로직을 여기 한 곳에 모은다.
 *
 * docs/avatar_core.js 는 이 파일의 복사본이다 — 수정 후 반드시
 *     cp static/avatar_core.js docs/
 * 로 동기화할 것. (app.py 기동 시 두 사본 해시를 비교해 불일치를 경고한다.)
 * drawface-live/docs/ 에도 vendored 사본이 있다. 그쪽은 위 해시 검증 밖이라 조용히 뒤처진다
 * (2026-07-31 실제로 발생) — 코어 수정 후 `drawface-live/scripts/sync_avatar_core.sh` 를 돌릴 것.
 * 잊어도 drawface-live 의 Vendor sync 워크플로가 push·매일 잡아낸다.
 *
 * 미러링 UI 문구는 한국어(기본)·영어를 함께 들고 있다 — 영어 페이지는 팩토리 호출 전에
 * AvatarCore.setLocale("en") 을 한 번 부른다(안 부르면 기존과 동일한 한국어).
 *
 * 일반 <script src> 로 로드되는 전역 스크립트이며 window.AvatarCore 를 정의한다.
 * 소비 페이지보다 먼저 로드할 것. 팩토리 함수들은 정의 시점에 DOM/전역에 접근하지 않고,
 * 페이지가 필요한 엘리먼트·접근자를 인자로 넘겨 호출한다.
 */
window.AvatarCore = (() => {

  // ---------- 로케일 (미러링 UI 문자열) ----------
  // 코어를 공유하는 두 리포의 언어가 다르다: ~/face 는 한국어(국내용), drawface-live 는 영어(HF Space 공개).
  // 페이지가 팩토리 호출 전에 AvatarCore.setLocale("en") 을 한 번 부른다. 기본값은 "ko".
  // 화면에 뜨는 문자열만 여기 모은다 — EMO_RULES 정규식·쇼케이스 대사의 한국어는 UI 가 아니라
  // 한국어 입력을 매칭·재생하는 데이터라 로케일과 무관하게 그대로 둔다.
  const STRINGS = {
    ko: {
      mirrorLoading: "미러링 모델 로드 중…",
      mirrorCalib: "캘리브레이션 — 정면·무표정으로 잠시 계세요",
      mirrorCalibCount: n => `정면을 보고 무표정을 유지해주세요… ${n}/30`,
      mirrorOn: "미러링 중 — 캐릭터가 따라합니다 (버튼으로 종료)",
      panelTitle: "내 얼굴 — 트래킹 미리보기",
      panelIdle: "미러링을 시작하면 표시됩니다",
      radar: { mouth: "입", smile: "미소", pucker: "오므림", eye: "눈", brow: "눈썹" },
    },
    en: {
      mirrorLoading: "Loading mirroring model…",
      mirrorCalib: "Calibrating — hold a neutral, front-facing pose",
      mirrorCalibCount: n => `Look straight ahead and keep a neutral face… ${n}/30`,
      mirrorOn: "Mirroring — your character follows you (button to stop)",
      panelTitle: "Your face — tracking preview",
      panelIdle: "Appears once mirroring starts",
      radar: { mouth: "Mouth", smile: "Smile", pucker: "Pucker", eye: "Eyes", brow: "Brows" },
    },
  };
  let locale = "ko";
  const T = () => STRINGS[locale];   // 호출 시점에 읽는다 — setLocale 이 늦게 와도 반영된다
  function setLocale(loc) { locale = STRINGS[loc] ? loc : "ko"; }

  // ---------- 내부 유틸 ----------
  const norm = s => s.toLowerCase().replace(/[_\-\s]/g, "");
  const clamp01 = v => Math.min(1, Math.max(0, v));                         // 0~1 로 자르기
  const avgLR = (W, base) => (W(base + "left") + W(base + "right")) / 2;   // 좌우 채널 평균
  const roundness = W => Math.max(W("mouthpucker"), W("mouthfunnel"));       // 오므림 세기
  const WARP_JAW_G = Math.exp(-(38 * 38) / (2 * 55 * 55));                   // jaw 변위장을 입 앵커(38px 위)에서 평가한 가우시안 (시그마 55 = 워프 셰이더와 동일)

  // 텍스트 감정 추론 (발화 시 자동 프리셋 — 세 페이지 동일 규칙)
  // 감정 사전: [정규식, 가중치]. 어간 문자클래스로 활용형을 함께 잡는다(슬프/슬퍼/슬펐/슬픈…).
  // 순서가 아니라 점수 합으로 뽑으므로 "ㅋㅋ 대박 웃겨"(joy 4.0 vs surprise 1.6)처럼 겹쳐도 옳게 갈린다.
  const EMO_RULES = {
    joy: [
      [/ㅋ{2,}|ㅎ{2,}/g, 2.2], [/하하|호호|ㅍㅎ|웃[겨긴음었]/g, 1.8],
      [/신[나난났]|행복|기[쁘뻐뻤쁜]|즐거|좋[아다은네았]|최고|사랑|반[가갑]|재[밌미]|고마[워웠운]|감사|😊|😄|🎉|👍|❤/g, 1.4],
      [/축하|성공|해냈|굿|짱/g, 1.2], [/!/g, 0.35],
    ],
    sad: [
      [/[ㅠㅜ]{2,}/g, 2.2],
      [/슬[프퍼펐픈]|우울|눈물|울[고었]|외로|쓸쓸|속상|서운|아쉽|안타깝|그립|😢|😭/g, 1.6],
      [/힘[들듦드]|아[파프픈]|지[친쳐쳤]|미안|죄송|망했|실패|포기/g, 1.2], [/\.{3,}/g, 0.4],
    ],
    angry: [
      [/화[나난가났]|짜증|열받|빡[쳐치친]|분노|억울|싫[어다은]|그만해|😠|😡/g, 1.8],
      [/최악|엉망|어이없|말도 안|참[나내]/g, 1.4],
    ],
    surprise: [
      [/헉|깜짝|놀[라랐랍]|대박|세상에|어머|웬일|믿[을기]\s*수\s*없|😲|😮/g, 1.6],
      [/[?!]{2,}/g, 1.0], [/진짜\?|정말\?|\?/g, 0.3],
    ],
    fear: [
      [/무[서섭섰]|겁[나난났이]|소름|섬뜩|오싹|공포|끔찍|😱|😨/g, 1.8],
      [/떨[려린렸]|불안|어떡해|어쩌지|살려/g, 1.2],
    ],
    shy: [
      [/부끄|쑥스|민망|수줍|창피|😳|☺️/g, 1.8],
      [/어머나|아이참|헤헤|히히/g, 1.0],
    ],
  };
  // 부정 표현 — "안 좋아", "좋지 않아", "재미없어"는 기쁨이 아니다.
  const NEGATION = /안\s*(좋|기쁘|행복|즐거|반가)|(좋|기쁘|행복|즐겁)지\s*(않|못)|재미\s*없|별로|싫증/;

  /** 텍스트 → {emo, intensity} | null. 모델 없이 점수 사전으로 추론(정적 데모에서도 동작). */
  function inferEmotion(text) {
    const score = {};
    for (const emo in EMO_RULES) score[emo] = 0;   // 사전에 감정 추가 시 자동 반영
    for (const emo in EMO_RULES) {
      for (const [re, w] of EMO_RULES[emo]) {
        const m = text.match(re);
        if (m) score[emo] += w * Math.min(m.length, 3);   // 반복 강조는 3회까지만 가산
      }
    }
    if (NEGATION.test(text)) { score.joy = 0; score.sad += 1.0; }
    let best = null, top = 0;
    for (const k in score) if (score[k] > top) { top = score[k]; best = k; }
    if (!best || top < 1.0) return null;                   // 신호가 약하면 중립 유지
    return { emo: best, intensity: Math.min(1, 0.45 + top * 0.16) };  // 0.45~1.0
  }

  // ---------- LLM 감정 분류 (규칙 매칭의 상위 경로) ----------
  // inferEmotion 정규식은 한국어 10문장 벤치에서 1/10 이었다 — 상주 LLM 에 물어본다.
  // 실패·타임아웃·503 은 전부 null 로 접어서 호출측이 규칙으로 떨어지게 한다.
  async function classifyEmotion(text, { timeoutMs = 4000 } = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch("/api/emotion", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }), signal: ctl.signal,
      });
      if (!res.ok) return null;
      const segs = (await res.json()).segments;
      return Array.isArray(segs) && segs.length ? segs : null;
    } catch {
      return null;      // 오프라인·중단·파싱 실패 — 규칙 폴백
    } finally {
      clearTimeout(timer);
    }
  }

  // ---------- 감정 → 목소리 톤 (얼굴만 웃고 목소리는 무표정한 괴리 해소) ----------
  // 비율값: rate=속도, pitch=음높이, volume=크기. edge-tts("+10%"/"+14Hz")와 브라우저 TTS(배수) 양쪽에서 사용.
  const VOICE_STYLE = {
    joy:      { rate: 0.10, pitch: 0.16 },
    sad:      { rate: -0.12, pitch: -0.12 },
    angry:    { rate: 0.06, pitch: -0.06, volume: 0.15 },
    surprise: { rate: 0.08, pitch: 0.22 },
    fear:     { rate: 0.10, pitch: 0.10, volume: -0.10 },   // 빠르고 떨리는 작은 소리
    shy:      { rate: -0.06, pitch: 0.06, volume: -0.15 },  // 작고 조심스럽게
    neutral:  {},
  };
  function voiceProsody(emo, intensity = 1) {
    const s = VOICE_STYLE[emo] || VOICE_STYLE.neutral;
    return { rate: (s.rate || 0) * intensity, pitch: (s.pitch || 0) * intensity,
             volume: (s.volume || 0) * intensity };
  }

  // 지수 평활 (무할당, in-place). target 에만 있는 새 키는 0에서 출발.
  function smoothStep(smooth, target) {
    for (const k in target) if (!(k in smooth)) smooth[k] = 0;
    for (const k in smooth) smooth[k] = 0.42 * smooth[k] + 0.58 * (target[k] || 0);
  }

  // 재생 중 발화 프레임 → 채널 가중치 (puppet·studio3d; docs 는 브라우저 TTS 타임라인이라 자체 구현)
  function weightsFromAnim(anim, audio) {
    const w = {};
    if (anim && !audio.paused && !audio.ended) {
      const f = anim.frames[Math.min(Math.floor(audio.currentTime * anim.fps), anim.frames.length - 1)];
      for (const [key, col] of anim.index) w[key] = f[col];
    }
    return w;
  }

  // ---------- 엔진 출력 셰이핑 (발화당 1회) ----------
  // A2F·NeuroSync 둘 다 턱을 거의 안 벌리고(jawOpen 최대 0.31~0.40 실측) 발화 내내 켜져 있는
  // 편향 채널이 있다(A2F jawRight 평균 0.17, NeuroSync browInnerUp 0.145). 엔진 쪽에는 세기
  // 손잡이가 없어서(A2F model.json 은 경로 설정뿐) 받은 프레임 전체를 여기서 정규화한다.
  const SHAPE = {
    a2f:       { gain: { jawopen: 2.3 }, kill: ["jawright", "jawleft"] },
    // browInnerUp·browOuterUp·eyeWide 는 NeuroSync가 발화 내내 놀란 표정을 상시 띄우는
    // 원인(상시 켜짐이 아니라 높은 값 주변에서 진동) — 게인<1로 눌러야 목표(평균<=0.05)에 닿는다.
    // 0.35/0.25 는 채널 프로브 실측(문장1, 자음·무음 섞인 발화)에서 평균이 0.047~0.060 으로
    // 목표(10% 마진 0.0455)에 걸쳐 있었다 — 재현 시 FAIL 이 나올 수 있는 수준이라 더 눌렀다.
    // worst-case(brow 0.060, eyewide 0.050) 기준 0.035 목표(마진선보다 23%↓, TTS 합성 변동
    // 여유분)로 역산: brow 0.35*(0.035/0.060)=0.204→0.20, eyewide 0.25*(0.035/0.050)=0.175→0.18.
    // 감정 프리셋(EMOTIONS.sad/surprise/fear 의 brow*/eyewide* 값 0.6~0.85)은 applyMax 로
    // 별도 max-결합되므로 이 게인과 무관하게 그대로 유지된다(makeEmotion.applyMax 참고).
    neurosync: { gain: { jawopen: 2.8, mouthfunnel: 0.7, browinnerup: 0.20, browouterupleft: 0.20,
                          browouterupright: 0.20, eyewideleft: 0.18, eyewideright: 0.18 }, kill: [] },
  };
  const BASELINE_PCT = 10;   // 채널별 하위 백분위 = "상시 켜져 있는" 성분

  function shapeAnim(anim, engine) {
    const prof = SHAPE[engine];
    // 이번 발화에 실제로 적용한 jawopen 게인을 기록 — 3D 모프용 증폭이라 2D(퍼펫)의 입 크기·
    // 몸짓 임계값처럼 원래(증폭 전) 스케일에 맞춰진 소비자가 이걸 나눠 되돌리는 용도.
    // 엔진 미상/증폭 없음이면 1(무보정).
    if (anim) anim.jawGain = (prof && prof.gain.jawopen) || 1;
    if (!prof || !anim || !anim.frames || !anim.frames.length || !anim.index) return anim;
    const kill = new Set(prof.kill);
    for (const [name, col] of anim.index) {
      if (kill.has(name)) {
        for (const f of anim.frames) f[col] = 0;
        continue;
      }
      const vals = anim.frames.map(f => f[col]);    // 스냅샷 — 아래서 f[col] 을 덮어써도 원본값 기준으로 계산
      const base = vals.slice().sort((a, b) => a - b)[Math.floor((vals.length - 1) * BASELINE_PCT / 100)];
      const gain = prof.gain[name] || 1;
      if (!base && gain === 1) continue;            // 손댈 것 없는 채널은 건너뛴다
      anim.frames.forEach((f, i) => {
        f[col] = clamp01((vals[i] - base) * gain);
      });
    }
    return anim;
  }

  // ---------- 감정 프리셋 (studio3d 버전이 superset 이라 그것으로 통합) ----------
  const EMOTIONS = {
    neutral: {},
    // eyesquint 가 0.25 이던 시절엔 눈꺼풀이 14%만 닫혀 눈웃음이 보이지 않았다 —
    // 진짜 웃음(Duchenne)의 핵심 채널이라 입꼬리에 준하는 값을 준다.
    joy: { mouthsmileleft: 0.55, mouthsmileright: 0.55, cheeksquintleft: 0.45, cheeksquintright: 0.45, eyesquintleft: 0.5, eyesquintright: 0.5 },
    sad: { mouthfrownleft: 0.5, mouthfrownright: 0.5, browinnerup: 0.7, mouthshrugupper: 0.2 },
    angry: { browdownleft: 0.85, browdownright: 0.85, nosesneerleft: 0.4, nosesneerright: 0.4, mouthpressleft: 0.4, mouthpressright: 0.4, jawforward: 0.25 },
    surprise: { browinnerup: 0.6, browouterupleft: 0.75, browouterupright: 0.75, eyewideleft: 0.8, eyewideright: 0.8, jawopen: 0.3 },
    fear: { eyewideleft: 0.7, eyewideright: 0.7, browinnerup: 0.85, mouthstretchleft: 0.35, mouthstretchright: 0.35, jawopen: 0.12 },
    // shy 의 eyelookdown 은 makeGaze 채널 결합을 타고 눈동자도 실제로 내려간다.
    shy: { mouthsmileleft: 0.3, mouthsmileright: 0.3, eyelookdownleft: 0.55, eyelookdownright: 0.55, mouthpressleft: 0.25, mouthpressright: 0.25 },
  };

  const CROSSFADE_S = 0.25;   // 문장 경계 표정 전환 시간

  // 감정 상태 + 버튼 배선. buttons/activeColor 는 페이지가 주입(2D #5b8cff / 3D #76b900).
  function makeEmotion(buttons, activeColor) {
    let emotion = EMOTIONS.neutral;
    let sticky = true;   // 수동 버튼=유지, 자동(발화 감정)=발화 끝나면 중립 복귀
    let hold = 1;        // 감정 세기 게이트(0~1). 자동 감정은 유휴 중 감쇠.
    // intensity: 자동 추론 시 감정 세기(0~1)로 프리셋 값을 스케일. 버튼 클릭은 항상 1.
    // 항상 새 객체로 스케일 — 공유 EMOTIONS 프리셋 앨리어싱 회피(v*1===v 라 무손실).
    // isSticky=false(자동 발화 감정)면 발화가 끝난 뒤 표정이 얼어붙지 않고 천천히 풀린다.
    let curKey = "neutral", curInt = 1;   // 몸짓 연동용 현재 감정 (current() 로 노출)
    let trackSeg = null, fadeFrom = {};   // 문장별 전환용 크로스페이드 상태
    function setEmotion(key, intensity = 1, isSticky = true) {
      const base = EMOTIONS[key] || EMOTIONS.neutral;
      emotion = {};
      for (const k in base) emotion[k] = base[k] * intensity;
      sticky = isSticky; hold = 1;
      curKey = key; curInt = intensity;
      buttons.forEach(x => x.style.background = x.dataset.emo === key ? activeColor : "#2a2a35");
    }
    buttons.forEach(b => { b.onclick = () => setEmotion(b.dataset.emo); });   // 버튼은 sticky 기본
    return {
      setEmotion,
      // 발화 중 문장이 바뀌면 표정도 바뀐다. track=[{start, emo, intensity}], tSec=audio.currentTime.
      // setEmotion 을 쓰지 않고 직접 섞는 이유: setEmotion 은 새 프리셋으로 통째 교체라
      // 경계에서 이전 표정이 한 프레임에 사라진다(그게 바로 없애려는 팝이다).
      // 문장이 바뀌는 순간의 표정을 박제해 두고 CROSSFADE_S 동안 새 프리셋과 겹쳐 넘긴다.
      followTrack(track, tSec) {
        if (!track || !track.length || sticky) return;   // 수동 버튼이 눌렸으면 사용자 의도가 우선
        let seg = track[0];
        for (const t of track) if (tSec >= t.start) seg = t;
        if (seg !== trackSeg) { fadeFrom = emotion; trackSeg = seg; }
        // fadeAt=seg.start: 경계에서 tSec≈seg.start(위 루프가 tSec>=seg.start 인 세그를 고르므로)이고,
        // 첫 호출은 speakWithEmotion 이 이미 세그 0 프리셋을 적용해둔 뒤라 k=1 이 곧바로 맞다.
        // 별도 변수로 관측 시각을 박제하지 않으므로 일시정지 후에도 페이드가 어긋나지 않는다.
        const k = clamp01((tSec - seg.start) / CROSSFADE_S);
        const base = EMOTIONS[seg.emo] || EMOTIONS.neutral;
        const blended = {};
        for (const key in fadeFrom) blended[key] = fadeFrom[key] * (1 - k);
        for (const key in base) blended[key] = (blended[key] || 0) + base[key] * seg.intensity * k;
        emotion = blended;
        hold = 1;      // 발화 중 유지, 끝나면 기존대로 감쇠
        curKey = seg.emo; curInt = seg.intensity;
      },
      // 감정 프리셋을 현재 평활값에 max-결합. speaking=발화 중이면 유지, 자동 감정은 유휴 시 ~1.5s 감쇠.
      applyMax(smooth, speaking) {
        hold = (sticky || speaking) ? 1 : hold * 0.98;
        for (const k in emotion) smooth[k] = Math.max(smooth[k] || 0, emotion[k] * hold);
      },
      // 현재 감정과 세기(표정과 같은 hold 감쇠를 공유) — makeHeadWander 몸짓 연동용.
      current() { return { key: curKey, level: curInt * hold }; },
    };
  }

  // ---------- 깜빡임 (버튼 + 자동) ----------
  // autoBlink:()=>bool, intervalMs:()=>ms 는 매 프레임 라이브 조회. duration/jitter 상수 페이지별
  // (puppet/docs 140·0.6·슬라이더, studio3d 150·0.8·3500). 상태는 클로저에 캡슐화.
  function makeBlink({ autoBlink, intervalMs = () => 4000, duration, jitter }) {
    let blinkAt = -1e9, nextAutoBlink = performance.now() + 4000;
    return {
      trigger() { blinkAt = performance.now(); },
      // 0~1 연속값. 예전엔 (now < blinkUntil ? 1 : 0) 사각파여서 눈이 순간이동으로 닫혔고,
      // 소비 측의 blink>0.5 이진 분기와 맞물려 부분 감김이 원리적으로 표현 불가능했다.
      // 실제 눈깜빡임은 감기는 쪽이 뜨는 쪽보다 빠르다 — 앞 35%를 감김, 나머지를 뜸에 준다.
      value(now) {
        if (autoBlink() && now > nextAutoBlink) {
          blinkAt = now;
          nextAutoBlink = now + intervalMs() * (0.7 + Math.random() * jitter);  // 자연스러운 지터
        }
        const t = (now - blinkAt) / duration;
        if (t < 0 || t > 1) return 0;
        return t < 0.35 ? t / 0.35 : 1 - (t - 0.35) / 0.65;
      },
    };
  }

  // ---------- 커서 시선 추적 ----------
  // el 의 pointermove/leave → {gx, gy} (-1..1). 프레임별 평활·엔진채널 결합은 makeGaze 또는 페이지 인라인.
  function makeCursorTracker(el) {
    const s = { gx: 0, gy: 0 };
    el.addEventListener("pointermove", e => {
      const r = el.getBoundingClientRect();
      s.gx = ((e.clientX - r.left) / r.width - 0.5) * 2;
      s.gy = ((e.clientY - r.top) / r.height - 0.5) * 2;
    });
    el.addEventListener("pointerleave", () => { s.gx = 0; s.gy = 0; });
    return s;
  }

  // ---------- 시선 결합 (슬라이더 > 엔진 채널 > 커서) + 0.15 평활 ----------
  // cursor: makeCursorTracker 결과, mulX/mulY: 커서 배율(puppet·docs 0.9/0.6, studio3d 0.8/0.5).
  // 반환: (sliderVal, W) => [gx, gy]. docs 는 슬라이더가 없어 sliderVal=0 고정으로 호출.
  function makeGaze(cursor, { mulX, mulY }) {
    let gx = 0, gy = 0, sacX = 0, sacY = 0, sacNext = 0;
    return (W) => {
      const now = performance.now();
      const chX = (W("eyelookoutright") + W("eyelookinleft") - W("eyelookoutleft") - W("eyelookinright")) / 2;
      const chY = (W("eyelookdownleft") + W("eyelookdownright") - W("eyelookupleft") - W("eyelookupright")) / 2;
      // 엔진채널·커서 다 없으면 유휴 → 눈동자 미세 saccade(죽은 눈 방지)
      const idle = !chX && !chY
        && Math.abs(cursor.gx) < 0.02 && Math.abs(cursor.gy) < 0.02;
      if (idle && now > sacNext) {
        sacNext = now + 1200 + Math.random() * 2000;
        const center = Math.random() < 0.35;  // 가끔 정면 복귀
        sacX = center ? 0 : (Math.random() - 0.5) * 0.5;
        sacY = center ? 0 : (Math.random() - 0.5) * 0.3;
      }
      // 우선순위: 유휴 saccade, 아니면 엔진채널 > 커서
      const tgtX = idle ? sacX : (chX || cursor.gx * mulX);
      const tgtY = idle ? sacY : (chY || cursor.gy * mulY);
      gx += (tgtX - gx) * 0.15;
      gy += (tgtY - gy) * 0.15;
      return [gx, gy];
    };
  }

  // ---------- 머리 워블 (2D: 발화 끄덕임 nod + 느린 표류 wander + 잔잔한 사인) ----------
  // shakeEl 에 CSS 변환 적용. sway 는 페이지가 넘김(발화 중 1, 아니면 0.5). studio3d 는 3D라 미사용.
  // 감정 → 몸짓 계수 (speed 배속·amp 진폭·droop px 아래로·beat 끄덕임 배율). 없는 키(neutral)는 전부 1/0.
  const EMO_MOTION = {
    joy:      { speed: 1.25, amp: 1.5,  droop: -2, beat: 1.3 },  // 들썩임 커지고 살짝 들림
    sad:      { speed: 0.55, amp: 0.5,  droop: 9,  beat: 0.4 },  // 고개 숙이고 느리고 작게
    angry:    { speed: 1.5,  amp: 1.25, droop: 0,  beat: 1.6 },  // 빠르고 절도 있게
    surprise: { speed: 1.2,  amp: 1.3,  droop: -4, beat: 1.0 },  // 번쩍 들림
    fear:     { speed: 1.45, amp: 0.7,  droop: 3,  beat: 0.6 },  // 움츠리고 잔떨림
    shy:      { speed: 0.8,  amp: 0.6,  droop: 5,  beat: 0.5 },  // 수줍게 숙임
  };

  // 2D 는 스프라이트라 yaw·pitch 를 회전시킬 수 없다 — 평행이동(%)으로 근사한다.
  // 코어 클램프(±0.5/±0.35rad)에서 최대 ±5%/±3.5% 이동 — 과하면 얼굴이 프레임을 벗어난다.
  const HEAD_SHIFT = 10;

  function makeHeadWander() {
    let nod = 0, wanderNext = 0, wanderR = 0, wanderY = 0, wanderGoalR = 0, wanderGoalY = 0;
    let beat = 0, beatTilt = 0, lowSince = 0;  // 강조 제스처: 구절 시작마다 끄덕임 임펄스
    let phR = 0, phY = 0, phB = 0, last = 0;   // 사인 위상 누적 — 감정 배속이 변해도 위상 연속(점프 없음)
    // head: 웹캠 머리 자세 [yaw, pitch, roll] (mirror.head(), 선택). 2D 라 roll 은 실제 기울기,
    // yaw·pitch 는 회전 대신 평행이동으로 근사한다 — 스프라이트를 3D 로 돌릴 수 없으므로.
    return function tick(shakeEl, now, jawopen, sway, emoState, head) {
      const m = emoState && EMO_MOTION[emoState.key], lv = m ? Math.min(emoState.level, 1) : 0;
      const speed = 1 + ((m ? m.speed : 1) - 1) * lv;
      const amp = 1 + ((m ? m.amp : 1) - 1) * lv;
      const beatG = 1 + ((m ? m.beat : 1) - 1) * lv;
      const droop = (m ? m.droop : 0) * lv;
      const dt = last ? Math.min((now - last) / 1000, 0.1) : 0;
      last = now;
      phR += dt * 0.9 * speed; phY += dt * 1.7 * speed; phB += dt * 1.2 * speed;
      nod = 0.85 * nod + 0.15 * jawopen;
      // 조용(≥250ms)하다 입이 열리는 순간 = 구절 시작 → 끄덕임 비트 + 고개 기울임 변주.
      // 매 음절마다가 아니라 pause 뒤 온셋에만 걸려 "말의 리듬"이 됨.
      if (jawopen < 0.1) {
        if (!lowSince) lowSince = now;
      } else {
        if (jawopen > 0.2 && lowSince && now - lowSince > 250) {
          beat = 1;
          beatTilt = (Math.random() - 0.5) * 0.02;
        }
        lowSince = 0;
      }
      beat *= 0.90;  // ~0.5s 감쇠
      if (now > wanderNext) {
        wanderNext = now + 2200 + Math.random() * 2500;
        wanderGoalR = (Math.random() - 0.5) * 0.03;
        wanderGoalY = (Math.random() - 0.5) * 5;
      }
      wanderR += (wanderGoalR - wanderR) * 0.02;
      wanderY += (wanderGoalY - wanderY) * 0.02;
      const breath = Math.sin(phB) * 2 * amp;  // ~5s 주기 호흡 — sway 무관(유휴에도 숨 쉼)
      // wander 도 amp 로 스케일 — 움츠린 감정(sad/fear)은 표류까지 작아져야 일관됨.
      const rot = Math.sin(phR) * 0.008 * sway * amp + wanderR * amp + nod * 0.015 + beatTilt * beat * beatG;
      const dy = Math.sin(phY) * 1.5 * sway * amp + wanderY * amp + nod * 3 + breath + beat * 7 * beatG + droop;
      // 머리 흔들림은 두 캔버스(WebGL base + 2D 오버레이)를 함께 감싼 래퍼에 CSS 변환으로 적용.
      // transform-origin=center + translateY(% of height) 조합이 기존 ctx translate/rotate와 수학적으로 동일.
      // 웹캠 머리 자세가 있으면 같은 문자열에 합성 — 유휴 흔들림 위에 실제 고개 방향이 얹힌다.
      const hR = head ? head[2] : 0;                      // 갸웃 = 실제 회전
      const hX = head ? head[0] * HEAD_SHIFT : 0;         // 좌우 = 평행이동(%)
      const hY = head ? head[1] * HEAD_SHIFT : 0;         // 상하 = 평행이동(%)
      shakeEl.style.transform =
        `rotate(${(rot + hR).toFixed(5)}rad) translate(${hX.toFixed(3)}%, ${(dy / 512 * 100 + hY).toFixed(4)}%)`;
    };
  }

  // ---------- 스프라이트 입모양 선택기 (개방도 우선 + 히스테리시스) ----------
  // W 를 생성 시 주입. targetMouth 는 puppet superset — docs 발화경로에서 mouthpress·mouthstretch=0 이라 정확히 환원.
  // pick(now) → {cur, prev, fade}: 크로스페이드 상태를 계산해 반환(내부 상태는 노출 안 함).
  function makeMouthPicker(W) {
    let curMouth = "closed", prevMouth = null, switchAt = 0, mouthCand = "closed", candSince = 0;
    const FADE_MS = 90;
    function targetMouth() {
      const jaw = W("jawopen");
      const round = roundness(W);
      const wide = Math.max(avgLR(W, "mouthsmile"), avgLR(W, "mouthstretch"));
      const press = avgLR(W, "mouthpress");
      if (jaw < 0.06) return (press > 0.2 || W("mouthclose") > 0.25) ? "M" : "closed";
      if (round > wide + 0.08) return jaw > 0.28 ? "O" : "U";
      if (jaw > 0.42) return "A";
      if (wide > 0.22) return jaw < 0.16 ? "I" : "E";
      return jaw < 0.14 ? "closed" : "E";
    }
    return {
      pick(now) {
        const t = targetMouth();
        if (t !== mouthCand) { mouthCand = t; candSince = now; }
        if (mouthCand !== curMouth && now - candSince >= 70) {  // 70ms 유지 시에만 전환
          prevMouth = curMouth; switchAt = now; curMouth = mouthCand;
        }
        return { cur: curMouth, prev: prevMouth, fade: Math.min(1, (now - switchAt) / FADE_MS) };
      },
    };
  }

  // ---------- 벡터 입 (근육 채널 → 윤곽 제어점 연속 변형) ----------
  // puppet 의 superset 공식으로 통합. 닫힘곡선 제어점 압력 = max(근육 press, mouthclose*0.5) 로
  // puppet(press 위주)·docs(mouthclose 위주) 양쪽 기존 픽셀을 회귀 없이 재현. frown 반영은 puppet 항.
  function drawVectorMouth(ctx, W, manifest, jawDy) {
    const st = manifest.mouthStyle || {};
    const [mcx, mcy0] = manifest.mouthCenter || [256, 340];
    const jaw = W("jawopen");
    const round = roundness(W);
    const pressM = avgLR(W, "mouthpress");        // 근육 압력 (openH 폐합용)
    const upperUp = avgLR(W, "mouthupperup");
    const lowerDown = avgLR(W, "mouthlowerdown");
    const smL = W("mouthsmileleft"), smR = W("mouthsmileright");
    const frL = W("mouthfrownleft"), frR = W("mouthfrownright");
    // 닫힘곡선 제어점 압력: 근육 press 와 mouthclose 유래 압력 중 강한 쪽 (하이브리드 — puppet·docs 양쪽 회귀 0)
    const pressCurve = Math.max(pressM, W("mouthclose") * 0.5);

    // 오므림(오/우): 세로 개방에 바닥값(+round*18)을 줘 낮은 턱에서도 둥근 구멍이 생기게 한다.
    const openH = Math.max(0, jaw * 58 + lowerDown * 10 + round * 18 - Math.max(pressM * 8, W("mouthclose") * 30));
    const wBase = st.width || 34;
    const halfL = wBase * (1 + 0.45 * W("mouthstretchleft") + 0.3 * smL - 0.6 * round);  // 오므림일수록 폭 좁힘
    const halfR = wBase * (1 + 0.45 * W("mouthstretchright") + 0.3 * smR - 0.6 * round);
    const cy = mcy0 + jawDy;
    const xL = mcx - halfL, xR = mcx + halfR;
    const yCL = cy - 2 - smL * 12 + frL * 12;   // 입꼬리 좌우 독립 (비대칭 표정)
    const yCR = cy - 2 - smR * 12 + frR * 12;
    // 오므림 강할수록 위/아래 곡선을 대칭(0.5/0.5)으로 → 납작한 렌즈가 아니라 동그란 O.
    const topF = 0.38 + 0.12 * round, botF = 1 - topF;
    const yU = cy - openH * topF - upperUp * 8;
    const yD = cy + openH * botF;

    ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.strokeStyle = st.line || "#3a2e2a";

    if (openH < 2.5) {  // 다문 입: 압력·미소·찡그림이 곡선 하나에 반영
      ctx.beginPath();
      ctx.moveTo(xL, yCL);
      ctx.quadraticCurveTo(mcx, cy + 5 + pressCurve * 4, xR, yCR);
      ctx.stroke();
      return;
    }
    const path = new Path2D();
    path.moveTo(xL, yCL);
    path.quadraticCurveTo(mcx, 2 * yU - (yCL + yCR) / 2, xR, yCR);   // 윗입술
    path.quadraticCurveTo(mcx, 2 * yD - (yCL + yCR) / 2, xL, yCL);   // 아랫입술
    path.closePath();
    ctx.fillStyle = st.fill || "#8a3535"; ctx.fill(path);
    ctx.save(); ctx.clip(path);
    // 오/우는 입술이 모여 이·혀가 거의 안 보임 — 오므림에 비례해 연속 감쇠(경계 팝 없음).
    const inner = Math.max(0, 1 - 1.3 * round);
    if (openH > 7) {  // 윗니 (inner=0 이면 높이 0 = 안 그려짐)
      ctx.fillStyle = st.teeth || "#ffffff";
      ctx.fillRect(xL, yU - 2, xR - xL, Math.min(9, openH * 0.32) * inner);
    }
    if (openH > 18) {  // 혀
      ctx.fillStyle = st.tongue || "#d97b7b";
      ctx.beginPath();
      ctx.ellipse(mcx, yD, (xR - xL) * 0.3, openH * 0.28 * inner, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
    ctx.stroke(path);
  }

  // ---------- 스프라이트 입 크로스페이드 ----------
  // 반드시 스프라이트 모드 브랜치에서만(프레임당 1회) 호출 — pick() 이 히스테리시스 상태를 전진시킴.
  // 전환 중(fade<1)이고 이전 스프라이트가 존재하면 α로 겹쳐 페이드, 아니면 현재만. jawDy 만큼 세로 이동.
  // 원본 입술을 위/아래 두 장으로 갈라 아랫입술만 내린다. 그 사이에 구강이 드러난다.
  // 원본을 그대로 두면 벌릴 틈이 없고(그림에 입 안쪽이 없다), 구강을 통째로 얹으면
  // 입술을 덮어버린다 — 둘 다 실측으로 확인하고 이 구조로 왔다.
  // 원본 입술만 변형한다 — 구강은 그리지 않는다.
  //
  // 다문 입 그림에는 벌어진 입의 픽셀이 아예 없다. 그 자리를 채우려면 없는 걸 만들어야
  // 하고, 만든 구강은 어떤 색·모양을 골라도 원본 화풍과 겉돈다(타원·사각형 둘 다 실측).
  // 그래서 입을 "벌리지" 않고 입술 자체를 늘였다 오므렸다 한다 — 모든 픽셀이 원본이다.
  // 만화 표현에서도 입을 안 벌리고 입술만 움직이는 립싱크는 흔하다.
  function drawSplitLips(ctx, parts, W, manifest, jawDy) {
    const lips = parts.mouth_lips;
    if (!lips) return false;
    const box = manifest.mouthBox || [0, 0, 0, 0];
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    // 벌림(jaw)은 세로로 늘리고, 오므림(round)은 가로로 좁힌다 — 입술 모양만으로
    // "아/오/우"의 차이가 읽힌다.
    const sy = 1 + W("jawopen") * 0.5 + avgLR(W, "mouthlowerdown") * 0.15;
    const sx = 1 - roundness(W) * 0.22 + avgLR(W, "mouthstretch") * 0.12;
    ctx.save();
    ctx.translate(cx, cy + jawDy);
    ctx.scale(sx, sy);
    ctx.translate(-cx, -cy);
    ctx.drawImage(lips, 0, 0);
    ctx.restore();
    return true;
  }

  function drawSpriteMouth(ctx, parts, picker, now, jawDy) {
    const { cur, prev, fade } = picker.pick(now);
    const drawM = name => parts[name] && ctx.drawImage(parts[name], 0, jawDy);
    if (fade < 1 && prev && parts["mouth_" + prev]) {
      ctx.globalAlpha = 1 - fade; drawM("mouth_" + prev);
      ctx.globalAlpha = fade; drawM("mouth_" + cur);
      ctx.globalAlpha = 1;
    } else {
      drawM("mouth_" + cur);
    }
  }

  // ---------- WebGL 얼굴 워핑 (base 정점 변위 그리드) ----------
  // 세분 평면(512×512, 48세그) + base 텍스처를 직교카메라로 픽셀 정합 렌더. 정점셰이더가 근육 채널값
  // (uniform)으로 가우시안 변위장을 적용해 턱·볼을 미세 변형. 색공간은 NoColorSpace + 순수 셰이더
  // 패스스루라 워프 0일 때 2D drawImage 와 픽셀 동일.
  // threeUrl 페이지별(/static/vendor/… 절대 vs ./vendor/… 상대 — 클래식 스크립트라 문서 기준 해석).
  // getParts/getManifest/W 는 페이지 상태 접근자.
  function makeWarp({ threeUrl, glCanvas, getParts, getManifest, W }) {
    return {
      ready: false,
      T: null, renderer: null, scene: null, camera: null, material: null, texture: null,
      // 입 오버레이 세로 이동: 워프 ON이면 jaw 변위장을 입 앵커에서 평가한 값(≈11px·jaw)으로 대체해
      // base 워프와 정확히 함께 움직이게 함(이중 이동 방지). OFF면 기존 jawDrop 사용. (38/55가 워프 시그마 결합)
      jawOverlayDy(jaw, warpOn, manifest) {
        return warpOn ? 14 * jaw * WARP_JAW_G : jaw * (manifest.jawDrop || 8);
      },
      vert: `
        uniform vec2 uJawC, uCornerL, uCornerR;
        uniform float uJaw, uSmileL, uSmileR, uRound, uFrownL, uFrownR, uSneer;
        uniform float uCheek, uJawFwd, uShrug;
        uniform vec2 uNoseC;
        varying vec2 vUv;
        float gk(vec2 p, vec2 c, float s){ vec2 d = p - c; return exp(-dot(d, d) / (2.0 * s * s)); }
        void main() {
          vUv = uv;
          vec2 img = vec2(position.x + 256.0, 256.0 - position.y);   // plane → 이미지 픽셀좌표(y down)
          vec2 disp = vec2(0.0);
          // 턱 드롭. 벡터 입을 안 쓰는(원본 입을 살린) 캐릭터는 이 워프가 유일한 립싱크라
          // 진폭이 커야 보인다 — 14px 로는 jawOpen 0.3 에서 4px 라 화면에서 안 읽혔다.
          disp += vec2( 0.0, 34.0) * uJaw    * gk(img, uJawC,    40.0);   // 턱 드롭
          disp += vec2(-7.0, -9.0) * uSmileL * gk(img, uCornerL, 32.0);   // 좌 입꼬리 (볼 당김)
          disp += vec2( 7.0, -9.0) * uSmileR * gk(img, uCornerR, 32.0);   // 우 입꼬리
          disp += vec2( 8.0,  0.0) * uRound  * gk(img, uCornerL, 32.0);   // 오므림 (안쪽)
          disp += vec2(-8.0,  0.0) * uRound  * gk(img, uCornerR, 32.0);
          disp += vec2(-3.0,  8.0) * uFrownL * gk(img, uCornerL, 32.0);   // 찡그림 (내림)
          disp += vec2( 3.0,  8.0) * uFrownR * gk(img, uCornerR, 32.0);
          // 코 찡긋 — 화남의 유일한 상단 얼굴 변형. 앵커가 입뿐이라 화남이 이미지를
          // 0픽셀 변형하던 문제를 푼다. 코를 위로 당겨 콧등에 주름이 잡히는 인상을 준다.
          disp += vec2( 0.0, -7.0) * uSneer  * gk(img, uNoseC,   30.0);
          // 아래 세 앵커는 입꼬리에서 유도한다 — 볼·입 중심은 입꼬리와의 상대 위치가
          // 캐릭터가 달라도 거의 일정해서, manifest 를 늘리지 않고도 자리를 잡을 수 있다.
          // 볼은 눈에서 멀리·좁게 잡는다. 눈은 base 위에 별도 스프라이트로 얹히는데 워프는
          // base 만 변형해서, 커널이 눈까지 닿으면 볼 홍조가 눈 밑으로 밀려 올라와 겹쳤다.
          vec2 cheekL = uCornerL + vec2(-18.0, -20.0);
          vec2 cheekR = uCornerR + vec2( 18.0, -20.0);
          vec2 mouthC = (uCornerL + uCornerR) * 0.5;
          disp += vec2( 0.0, -5.0) * uCheek  * gk(img, cheekL,   26.0);   // 볼 올림 (진짜 웃음)
          disp += vec2( 0.0, -5.0) * uCheek  * gk(img, cheekR,   26.0);
          disp += vec2( 0.0,  5.0) * uJawFwd * gk(img, uJawC,    48.0);   // 턱 내밈 (화남)
          disp += vec2( 0.0, -5.0) * uShrug  * gk(img, mouthC,   22.0);   // 윗입술 삐죽 (슬픔)
          vec2 pos = position.xy;
          pos.x += disp.x; pos.y -= disp.y;                          // 이미지 y-down → plane y-up
          gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 0.0, 1.0);
        }`,
      frag: `
        uniform sampler2D uTex;
        varying vec2 vUv;
        void main() { gl_FragColor = texture2D(uTex, vUv); }`,   // colorspace include 없음 → sRGB 바이트 그대로
      async init() {
        try {
          const T = await import(threeUrl);
          this.T = T;
          // preserveDrawingBuffer: 움짤 녹화가 drawImage 로 gl 캔버스를 캡처할 때 빈 프레임 방지(정석 옵션).
          this.renderer = new T.WebGLRenderer({ canvas: glCanvas, alpha: true, antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: true });
          // 셰이더 링크 실패는 three가 throw하지 않고 콘솔 로깅만 함 → 이 콜백으로 감지해 폴백 전환.
          this.renderer.debug.onShaderError = () => { this.ready = false; };
          this.renderer.setClearColor(0x000000, 0);
          this.renderer.setSize(512, 512, false);
          this.scene = new T.Scene();
          this.camera = new T.OrthographicCamera(-256, 256, 256, -256, -1, 1);
          this.material = new T.ShaderMaterial({
            uniforms: {
              uTex: { value: null },
              uJawC: { value: new T.Vector2(256, 378) },
              uCornerL: { value: new T.Vector2(220, 340) },
              uCornerR: { value: new T.Vector2(292, 340) },
              uNoseC: { value: new T.Vector2(256, 300) },
              uSneer: { value: 0 },
              uCheek: { value: 0 },
              uJawFwd: { value: 0 },
              uShrug: { value: 0 },
              uJaw: { value: 0 }, uSmileL: { value: 0 }, uSmileR: { value: 0 },
              uRound: { value: 0 }, uFrownL: { value: 0 }, uFrownR: { value: 0 },
            },
            vertexShader: this.vert, fragmentShader: this.frag, transparent: true, side: T.DoubleSide,
          });
          this.scene.add(new T.Mesh(new T.PlaneGeometry(512, 512, 48, 48), this.material));
          this.ready = true;
          this.setCharacter();   // 경쟁 처리: base가 이미 로드됐으면 여기서 텍스처 설정
          // init 시 강제 1회 렌더 → 셰이더 컴파일/링크를 지금 유발. 실패하면 throw(catch) 또는
          // onShaderError가 ready=false로 내려 렌더 루프가 base를 2D로 그리는 폴백이 확실히 작동.
          this.renderer.render(this.scene, this.camera);
        } catch (e) {
          this.ready = false;    // WebGL 불가/로드 실패 → 조용히 폴백
        }
      },
      setCharacter() {
        const parts = getParts(), manifest = getManifest();
        if (!this.ready || !parts.base) return;
        const T = this.T;
        if (this.texture) this.texture.dispose();
        // 정적 캐릭터 base는 Image, 드래그앤드랍 유저 캐릭터 base는 canvas → CanvasTexture로 수용.
        this.texture = (parts.base instanceof HTMLCanvasElement)
          ? new T.CanvasTexture(parts.base)
          : new T.Texture(parts.base);
        this.texture.colorSpace = T.NoColorSpace;   // sRGB 바이트 그대로 업로드 (GPU 선형화 안 함)
        this.texture.premultiplyAlpha = false;
        this.texture.minFilter = T.LinearFilter;
        this.texture.magFilter = T.LinearFilter;
        this.texture.generateMipmaps = false;
        this.texture.needsUpdate = true;
        this.material.uniforms.uTex.value = this.texture;
        const mc = manifest.mouthCenter || [256, 340];
        const mw = (manifest.mouthStyle && manifest.mouthStyle.width) || 30;
        this.material.uniforms.uJawC.value.set(mc[0], mc[1] + 38);
        this.material.uniforms.uCornerL.value.set(mc[0] - mw * 1.2, mc[1]);
        this.material.uniforms.uCornerR.value.set(mc[0] + mw * 1.2, mc[1]);
        // 코는 입 중심에서 위로 — manifest 에 noseCenter 가 있으면 그걸 쓴다
        const nc = manifest.noseCenter || [mc[0], mc[1] - 44];
        this.material.uniforms.uNoseC.value.set(nc[0], nc[1]);
      },
      render() {
        if (!this.ready || !this.texture) return;
        const u = this.material.uniforms;
        u.uJaw.value = W("jawopen");
        u.uSmileL.value = W("mouthsmileleft");
        u.uSmileR.value = W("mouthsmileright");
        u.uRound.value = roundness(W);
        u.uFrownL.value = W("mouthfrownleft");
        u.uFrownR.value = W("mouthfrownright");
        u.uSneer.value = avgLR(W, "nosesneer");
        u.uCheek.value = avgLR(W, "cheeksquint");
        u.uJawFwd.value = W("jawforward");
        u.uShrug.value = W("mouthshrugupper");
        this.renderer.render(this.scene, this.camera);
      },
    };
  }

  // ---------- 발화 요청 → 결과 (내부) ----------
  // 서버가 동기 응답(0.6초급 작업) — 잡 폴링 제거로 발화당 ~0.2~0.35s 단축.
  async function speakRT({ text, voice, engine, prosody }) {
    const res = await fetch("/api/speak_rt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice, engine, ...(prosody || {}) }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    return res.json();
  }

  // ---------- 발화 플로우 (puppet·studio3d 공용) ----------
  // speakRT → anim 조립(head 포함, 2D는 미사용이라 무해) → onAnim(재생 전 반영) → audio.src → play.
  // onAnim 은 play 로딩 창 동안 렌더 루프가 최신 anim 을 보게 하려고 src/play 이전에 호출(원본 순서 유지).
  // 페이지 핸들러는 autoEmo·버튼 disable·에러 setStatus 만 담당.
  async function speakFlow({ text, voice, engine, audioEl, onAnim, prosody }) {
    const r = await speakRT({ text, voice, engine, prosody });
    const anim = { fps: r.fps, frames: r.frames, head: r.head, index: r.names.map((n, i) => [norm(n), i]) };
    shapeAnim(anim, engine);          // 엔진 출력 정규화 (입 벌림·편향 제거)
    anim.sentences = r.sentences;     // 문장 시작 시각 (없을 수 있음)
    if (onAnim) onAnim(anim);
    audioEl.src = r.audio_url;
    await audioEl.play();
    return anim;
  }

  // ---------- 감정 결정 + 발화 (puppet·studio3d 공용) ----------
  // emotion 지정(LLM 판단) 있으면 그대로, 없으면 LLM 분류 → 실패 시 규칙 추론. autoEmo(호출 시점 boolean) 켜져 있으면
  // emo(makeEmotion 인스턴스) 프리셋 + 목소리 톤 적용 후 speakFlow. voice/engine 은 호출 시점 값.
  async function speakWithEmotion({ text, emotion, autoEmo, emo, voice, engine, audioEl, onAnim }) {
    let segs = null;
    let r = emotion ? { emo: emotion, intensity: 0.9 } : null;
    if (!r && autoEmo) {
      // 직렬 호출: 목소리 톤(prosody)이 TTS 요청 파라미터라 감정을 먼저 알아야 한다.
      segs = await classifyEmotion(text);
      r = segs ? { emo: segs[0].emo, intensity: segs[0].intensity } : inferEmotion(text);
    } else if (!r) {
      r = inferEmotion(text);
    }
    let prosody = null;
    // 명시 감정(대화 모드 LLM·클릭 반응)은 autoEmo 와 무관하게 적용한다 — 예전엔 autoEmo 가
    // 꺼져 있으면 넘어온 emotion 까지 무시돼 표정·목소리 톤이 둘 다 사라졌다.
    if (r && (autoEmo || emotion)) {
      emo.setEmotion(r.emo, r.intensity, false);   // 자동 감정 — 발화 끝나면 중립 복귀
      prosody = voiceProsody(r.emo, r.intensity);
    }
    const anim = await speakFlow({ text, voice, engine, audioEl, onAnim, prosody });
    // 문장별 감정 전환은 두 배열의 길이가 맞을 때만 (분할 결과가 같다는 전제 확인)
    if (segs && anim.sentences && anim.sentences.length === segs.length) {
      anim.emotionTrack = anim.sentences.map((s, i) => ({
        start: s.start, emo: segs[i].emo, intensity: segs[i].intensity }));
    }
    return anim;
  }

  // ---------- 자동 쇼케이스 (첫 방문자 유휴 시 인사·감정 시연) ----------
  // 오늘 넣은 표현력(감정 7종·몸짓·입 오므림)을 방문자가 아무것도 안 눌러도 보게 하는 유도.
  const SHOWCASE_SCRIPT = [
    { text: "안녕하세요! 저는 그림에서 태어난 아바타예요.", emo: "joy" },
    { text: "이렇게 활짝 웃기도 하고,", emo: "joy" },
    { text: "시무룩해지기도,", emo: "sad" },
    { text: "깜짝 놀라기도,", emo: "surprise" },
    { text: "무서워하기도,", emo: "fear" },
    { text: "부끄러워하기도 한답니다.", emo: "shy" },
    { text: "위에 문장을 입력하면 제가 말해드릴게요!", emo: "joy" },
  ];
  // playStep(step) → Promise(발화 완료). 발화 방식은 페이지가 주입(docs=speechSynthesis).
  // 유휴 감지·중단 트리거는 엘리먼트가 페이지마다 달라 페이지가 소유 — 여긴 순차 재생 엔진만.
  function makeShowcase(playStep, script = SHOWCASE_SCRIPT) {
    let running = false, cancelled = false;
    return {
      get running() { return running; },
      async play() {
        if (running) return;
        running = true; cancelled = false;
        for (const step of script) {
          if (cancelled) break;
          await playStep(step);
          if (cancelled) break;
          await new Promise(r => setTimeout(r, 220));   // 스텝 사이 짧은 숨
        }
        running = false;
      },
      stop() { cancelled = true; },
    };
  }

  // ---------- 캐릭터 클릭 반응 (아바타를 누르면 감정 섞인 한마디) ----------
  const REACTIONS = [
    { text: "우와, 깜짝이야!", emo: "surprise" },
    { text: "헤헤, 간지러워요.", emo: "joy" },
    { text: "아이, 부끄럽게 왜 그래요.", emo: "shy" },
    { text: "안녕하세요! 반가워요.", emo: "joy" },
    { text: "어? 왜 그러세요?", emo: "surprise" },
    { text: "으, 살살 해주세요.", emo: "fear" },
  ];
  let _lastReact = -1;
  function pickReaction() {   // 직전과 다른 반응을 뽑아 연속 중복 방지
    let i;
    do { i = Math.floor(Math.random() * REACTIONS.length); } while (i === _lastReact && REACTIONS.length > 1);
    _lastReact = i;
    return REACTIONS[i];
  }

  // ---------- 분석 패널 (비교군): 내 얼굴 + 478점 랜드마크 + 구동 채널값 ----------
  // 어느 페이지든 컨테이너 하나 주면 동일 패널 — 스타일 인라인이라 페이지 CSS 의존 없음.
  // 반환된 draw(W) 를 렌더 루프에서 매 프레임 호출 (W = 채널 접근자). 컨테이너 숨김이면 즉시 반환.
  // 미리보기: 웹캠 + 얼굴 메시(어디를 잡았나) + 오각형(얼마나 움직였나).
  // 값 막대를 쌓지 않는 이유 — 채널 계수는 전송 데이터이지 읽는 정보가 아니고(Live Link Face·
  // VSeeFace 도 메시만 겹쳐 보여준다), 모델 내부 채널명·0.00 정밀도를 UI 에 노출하면 값을
  // 그대로 렌더한 티가 난다. 대신 다섯 축이 한 덩어리로 일그러지는 모양을 보여준다:
  // 개별 수치는 못 읽어도 "지금 살아서 반응한다"가 한눈에 들어온다.
  //
  // 시선은 여기 없다. 크기가 아니라 방향(좌우·상하)이라 축 하나에 올릴 수 없고,
  // 메시 위 홍채 점이 이미 그 자체로 보여준다.
  // 첫 원소는 라벨이 아니라 STRINGS[*].radar 의 키다 — 표시 문구는 로케일에서 온다.
  const RADAR_CHS = [
    ["mouth", W => W("jawopen")],
    ["smile", W => (W("mouthsmileleft") + W("mouthsmileright")) / 2],
    ["pucker", W => W("mouthpucker")],
    ["eye", W => (W("eyeblinkleft") + W("eyeblinkright")) / 2],
    ["brow", W => W("browinnerup")],
  ];
  function makeMirrorPanel(mirror, mount) {
    let lastFrame = -1;   // 마지막으로 미리보기에 그린 웹캠 프레임 번호
    mount.innerHTML = '<div style="font-size:.85rem;font-weight:600;color:#9a9ab0;margin:2px 0 8px">' + T().panelTitle + '</div>';
    const cv = document.createElement("canvas");
    cv.width = 320; cv.height = 240;
    cv.style.cssText = "width:100%;border-radius:12px;border:1px solid #2a2a35;background:#0d0d12;display:block";
    mount.appendChild(cv);
    const ctx = cv.getContext("2d");

    const rv = document.createElement("canvas");
    rv.width = 320; rv.height = 218;
    rv.style.cssText = "width:100%;margin-top:8px;display:block";
    mount.appendChild(rv);
    const rc = rv.getContext("2d");
    // 12시부터 시계방향. 축 순서를 바꾸면 같은 표정이 다른 모양이 되므로 고정이다.
    const N = RADAR_CHS.length, CX = 160, CY = 118, R = 90;
    const AX = i => -Math.PI / 2 + i * 2 * Math.PI / N;
    const poly = (get, close) => {
      rc.beginPath();
      for (let i = 0; i < N; i++) {
        const r = get(i), a = AX(i);
        const x = CX + Math.cos(a) * r, y = CY + Math.sin(a) * r;
        i ? rc.lineTo(x, y) : rc.moveTo(x, y);
      }
      if (close !== false) rc.closePath();
    };

    return {
      canvas: cv,   // 동시 구동 화면의 녹화 합성용
      draw(W) {
        if (!mount.offsetParent) return;   // 숨김 상태 — 일 안 함
        const d = mirror.debug();
        // 웹캠 프레임이 그대로면 같은 그림을 다시 그리게 된다 — 렌더 루프(60~144fps)가
        // 추론(~30fps)보다 빨라 4~5배 헛일. 오각형은 값이 계속 변하므로 매 프레임 갱신한다.
        if (d.frame !== lastFrame || !mirror.on) {
          lastFrame = d.frame;
          const w = cv.width, h = cv.height;
          ctx.fillStyle = "#0d0d12"; ctx.fillRect(0, 0, w, h);
          if (!mirror.on || !d.video || d.video.readyState < 2) {
            ctx.fillStyle = "#9a9ab0"; ctx.font = "13px sans-serif"; ctx.textAlign = "center";
            ctx.fillText(T().panelIdle, w / 2, h / 2);
          } else {
            ctx.save(); ctx.scale(-1, 1); ctx.drawImage(d.video, -w, 0, w, h); ctx.restore();   // 거울 반전
            if (d.lm) {
              const X = i => (1 - d.lm[i].x) * w, Y = i => d.lm[i].y * h;
              if (d.mesh) {
                // MediaPipe 표준 토폴로지(2556 세그먼트)를 한 경로로 모아 한 번에 stroke —
                // 개별 stroke 면 프레임을 먹는다.
                ctx.beginPath();
                for (const c of d.mesh) {
                  const a = c.start ?? c[0], b = c.end ?? c[1];
                  ctx.moveTo(X(a), Y(a)); ctx.lineTo(X(b), Y(b));
                }
                ctx.strokeStyle = "rgba(232,232,239,.22)"; ctx.lineWidth = 0.5; ctx.stroke();
              } else {
                ctx.fillStyle = "rgba(232,232,239,.5)";   // 구버전 tasks-vision 폴백
                for (let i = 0; i < 468; i++) ctx.fillRect(X(i) - .5, Y(i) - .5, 1.5, 1.5);
              }
              ctx.fillStyle = "#ffb03a";   // 홍채 10점 = 시선 계산의 실제 입력
              for (let i = 468; i < d.lm.length; i++) ctx.fillRect(X(i) - 1.5, Y(i) - 1.5, 3, 3);
            }
          }
        }

        rc.clearRect(0, 0, rv.width, rv.height);
        rc.strokeStyle = "rgba(232,232,239,.13)"; rc.lineWidth = 1;
        for (const f of [0.5, 1]) { poly(() => R * f); rc.stroke(); }
        rc.beginPath();
        for (let i = 0; i < N; i++) { rc.moveTo(CX, CY); rc.lineTo(CX + Math.cos(AX(i)) * R, CY + Math.sin(AX(i)) * R); }
        rc.stroke();

        poly(i => Math.max(0, Math.min(1, RADAR_CHS[i][1](W))) * R);
        rc.fillStyle = "rgba(255,176,58,.20)"; rc.fill();
        rc.strokeStyle = "#ffb03a"; rc.lineWidth = 1.5; rc.stroke();

        rc.fillStyle = "#9a9ab0"; rc.font = "13px sans-serif"; rc.textAlign = "center"; rc.textBaseline = "middle";
        for (let i = 0; i < N; i++) {
          const a = AX(i);
          rc.fillText(T().radar[RADAR_CHS[i][0]], CX + Math.cos(a) * (R + 22), CY + Math.sin(a) * (R + 16));
        }
      },
    };
  }

  // ---------- 2D 캐릭터 그리기 (소년·소녀 공용) ----------
  // 파츠 스프라이트(눈썹·눈·동공) + 벡터 입을 한 번에. 워프를 쓰는 페이지는 warp 를 넘기면
  // 배경/턱 오프셋을 워프 기준으로, 안 넘기면 흰 배경 + manifest.jawDrop 폴백으로 그린다.
  //
  // ctx: 2D 컨텍스트(512²), parts: 이미지 맵, manifest: 캐릭터 튜닝값,
  // W: 채널 접근자, blink: 깜빡임(자동+미러 max 결합), gaze: [gx, gy],
  // opts.warp: makeWarp 인스턴스(선택), opts.clearBg: 배경 채우기 여부(클린 모드면 false)
  // 눈썹 기울기 최대 각(rad) — 12px 이동과 균형이 맞는 크기로 실측 조정.
  const BROW_TILT = 0.22;

  // 눈썹을 자기 중심에서 회전시켜 그린다. 스프라이트는 512² 전체 캔버스에 그려져 있으므로
  // 회전 피벗은 manifest 의 눈 중심(없으면 캔버스 중앙 위쪽)을 쓴다.
  function drawBrow(name, dy, tilt, manifest) {
    const img = _partsRef && _partsRef[name];
    if (!img) return;
    const c = _ctxRef;
    if (!tilt) { c.drawImage(img, 0, dy); return; }
    const [px, py] = manifest.browPivot || [256, 250];
    c.save();
    c.translate(px, py + dy); c.rotate(tilt); c.translate(-px, -py);
    c.drawImage(img, 0, 0);
    c.restore();
  }
  let _partsRef = null, _ctxRef = null;   // drawBrow/drawEyes 가 쓰는 프레임 지역 참조

  // 눈 스프라이트의 열별 프로파일. 캐릭터당 1회 계산해 캐싱한다.
  //
  // 알파 bbox 를 쓰면 안 된다 — 빌더가 클릭 좌표 ±반경 사각형을 통째로 잘라 넣어서 알파
  // 경계가 곧 클릭 상자이고 실제 눈과 다르다. 그래서 잉크(어두운 픽셀)를 기준으로 잡되,
  // 최대 연결성분만 남긴다 — 소녀 캐릭터는 앞머리 획 141px 이 눈 상자에 걸쳐 있어
  // 잉크 bbox 가 58px 로 부풀고 눈이 14% 과압축됐다. 그 획은 눈이 아니므로 화면에는
  // 그대로 두되 애니메이션만 안 받는 게 맞다.
  //
  // 화풍은 두 지표로 갈린다(실측): 열마다 잉크가 끊겨 여러 런이 되는 비율(=내부가 비었다),
  // 그리고 채움률. 만화눈 0.84/0.29, 점눈 0.00/0.69~0.78, 실눈 0.00/0.43~0.47 로
  // 마진이 넓다.
  const INK_MAX = 300;   // r+g+b 합이 이 미만이면 '획'. character_builder.snap_eye_box 와 같은 값 —
                         // 한쪽만 바꾸면 빌더가 자른 상자와 렌더가 재는 프로파일이 다른 눈을 가리킨다.
  const MIN_VIS = 3;     // 완전히 감겨도 남기는 최소 높이(px). 512² 스프라이트(CANVAS) 기준.
  const EYE_SIDES = [["L", "eye_L_open", "pupil_L"], ["R", "eye_R_open", "pupil_R"]];
  const _pairCache = new WeakMap();
  let _scratch = null;

  // 양쪽 눈을 함께 분류한다. 한 캐릭터의 두 눈은 같은 화풍이므로 판정도 하나여야 하는데,
  // 눈마다 따로 재면 지표가 임계 근처인 그림에서 좌우가 갈린다 — 돼지·졸라맨이 실제로
  // blob/line 으로 갈려 한쪽 눈만 감겼다(채움률 0.69 vs 0.58, 임계 0.6).
  //
  // 반환: { cls, L, R } — cls 는 공통, L/R 은 각자의 열 프로파일(선눈이면 null).
  // 캐시 키는 왼쪽 눈 이미지. loadCharacter 가 캐릭터 전환 때만 새 Image 를 만들어
  // 두 이미지의 수명이 같으므로 한쪽만 키로 잡아도 짝이 어긋나지 않는다.
  function eyeProfiles(parts) {
    const eL = parts.eye_L_open;
    if (!eL) return { cls: null, L: null, R: null };
    const hit = _pairCache.get(eL);
    if (hit) return hit;
    let out = { cls: null, L: null, R: null };
    try {
      const raws = [_scan(eL), parts.eye_R_open ? _scan(parts.eye_R_open) : null];
      const seen = raws.filter(Boolean);
      if (seen.length) {
        // 화풍은 두 지표로 갈린다(실측): 열마다 잉크가 끊겨 여러 런이 되는 비율
        // (=내부가 비었다), 그리고 채움률. 만화눈 0.84/0.29, 점눈 0.00/0.69~0.78,
        // 실눈 0.00/0.43~0.47. 양쪽 눈의 픽셀을 합쳐 한 번 판정한다.
        const sum = k => seen.reduce((a, r) => a + r[k], 0);
        const cls = sum("multi") / sum("cols") >= 0.4 ? "outline"
                  : (sum("area") / sum("boxArea") >= 0.6 ? "blob" : "line");
        out = { cls, L: _finish(raws[0], cls), R: _finish(raws[1], cls) };
      }
    } catch { /* 픽셀을 못 읽으면 폴백 */ }
    _pairCache.set(eL, out);
    return out;
  }

  // 선눈(획 자체가 그림)은 null 이 된다 — 지킬 굵기가 곧 눈 전체라 어떤 lid 에서도
  // 움직일 여지가 없다. 소비 측의 `!p` 폴백이 그대로 "원본을 그린다"라서, 별도의
  // travel 배열이나 플래그 없이 같은 동작이 나온다.
  function _finish(raw, cls) {
    if (!raw || cls === "line") return null;
    const { x0, x1, top, bot, cap, cy0, cy1 } = raw;
    const prof = { cls, x0, x1, top0: cy0, bot1: cy1 + 1, top };
    if (cls === "blob") {
      // 점은 지킬 획이 없다 — 아래 MIN_VIS 만 남기고 위에서 가린다.
      const travel = new Int16Array(top.length);
      for (let x = x0; x <= x1; x++) {
        if (top[x] >= 0) travel[x] = Math.max(0, bot[x] - top[x] + 1 - MIN_VIS);
      }
      prof.travel = travel;
    } else {
      // 윤곽눈은 눈알 전체를 눌러 내리므로 열별 이동량이 아니라 최소 잔여 높이만 필요하다.
      const caps = [];
      for (let x = x0; x <= x1; x++) if (top[x] >= 0) caps.push(cap[x]);
      caps.sort((a, b) => a - b);
      prof.capMed = caps[caps.length >> 1] || MIN_VIS;
    }
    return prof;
  }

  function _scan(img) {
    const W = img.width, H = img.height;
    if (!_scratch || _scratch.canvas.width !== W || _scratch.canvas.height !== H) {
      const c = document.createElement("canvas");
      c.width = W; c.height = H;
      _scratch = c.getContext("2d", { willReadFrequently: true });
    }
    _scratch.clearRect(0, 0, W, H);
    _scratch.drawImage(img, 0, 0);
    const d = _scratch.getImageData(0, 0, W, H).data;
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let i = 3, p = 0; p < W * H; p++, i += 4) {
      if (d[i] > 0 && d[i - 3] + d[i - 2] + d[i - 1] < INK_MAX) {
        const x = p % W, y = (p / W) | 0;
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
    if (x1 < 0) return null;

    // 최대 연결성분(8-이웃)만 남긴다 — 소녀 캐릭터는 앞머리 획 141px 이 눈 상자에 걸쳐
    // 잉크 bbox 가 58px 로 부풀고 눈이 14% 과압축됐다. 그 획은 눈이 아니므로 화면에는
    // 그대로 두되 애니메이션만 안 받는 게 맞다.
    // 라벨 배열은 눈 bbox 크기로만 잡는다(512² 는 1MB memset). 0=미라벨이라 fill 불필요.
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    const isInk = (x, y) => {
      const i = (y * W + x) * 4;
      return d[i + 3] > 0 && d[i] + d[i + 1] + d[i + 2] < INK_MAX;
    };
    const lab = new Int32Array(bw * bh);
    const stack = [];
    let best = 0, bestN = 0;
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const s = (y - y0) * bw + (x - x0);
        if (lab[s] || !isInk(x, y)) continue;
        const id = s + 1;
        let n = 0;
        stack.push(x, y); lab[s] = id;
        while (stack.length) {
          const qy = stack.pop(), qx = stack.pop(); n++;
          for (let dy = -1; dy <= 1; dy++) {
            for (let dx = -1; dx <= 1; dx++) {
              const nx = qx + dx, ny = qy + dy;
              if (nx < x0 || nx > x1 || ny < y0 || ny > y1) continue;
              const t = (ny - y0) * bw + (nx - x0);
              if (!lab[t] && isInk(nx, ny)) { lab[t] = id; stack.push(nx, ny); }
            }
          }
        }
        if (n > bestN) { bestN = n; best = id; }
      }
    }
    if (!best) return null;

    // 열별 최상단/최하단 + 최상단 런 두께, 그리고 분류 지표
    const top = new Int16Array(W).fill(-1);
    const bot = new Int16Array(W), cap = new Int16Array(W);
    let cols = 0, multi = 0, area = 0, cy0 = H, cy1 = -1;
    for (let x = x0; x <= x1; x++) {
      let first = -1, last = -1, runs = 0, capH = 0, prev = -2;
      for (let y = y0; y <= y1; y++) {
        if (lab[(y - y0) * bw + (x - x0)] !== best) continue;
        if (first < 0) first = y;
        last = y; area++;
        if (y !== prev + 1) runs++;
        if (runs === 1) capH++;
        prev = y;
      }
      if (first < 0) continue;
      cols++; if (runs > 1) multi++;
      top[x] = first; bot[x] = last; cap[x] = capH;
      if (first < cy0) cy0 = first;
      if (last > cy1) cy1 = last;
    }
    if (!cols) return null;
    // 분류는 여기서 하지 않는다 — 양쪽 눈 지표를 합쳐 eyeProfiles 가 한 번에 판정한다.
    // 채움률 분모는 잉크 전체 bbox 가 아니라 **최대 성분이 차지하는 열** 기준이다.
    // 전체 bbox 를 쓰면 성분 밖 잡티가 폭을 늘려 값이 반토막 난다(돼지 오른눈: 전체
    // 폭 20 인데 점은 8열 → 0.89 가 0.36 으로 떨어져 점눈이 선눈으로 오분류됐다).
    return { x0, x1, top, bot, cap, cy0, cy1,
             cols, multi, area, boxArea: cols * (cy1 - cy0 + 1) };
  }

  // 눈꺼풀이 내려오는 렌더. 눈알을 찌그러뜨리지 않고 위에서 가린다 — Live2D 도
  // "윗속눈썹을 아래속눈썹 위치까지 내리고 눈알은 클리핑"이라고 정의한다.
  // 화풍마다 가리는 방식이 다른 건 지킬 것이 다르기 때문이다(내부 vs 획 굵기).
  function occludeEye(dst, img, p, lid) {
    if (!p) { dst.drawImage(img, 0, 0); return; }

    if (p.cls === "outline") {
      // 흰자가 있는 눈은 눈알째 눌러 내린다. 열별로 가리기만 하면 흰자가 뚫려
      // '빈 테두리 원'이 되고 눈꺼풀이 내려오는 인상이 안 난다 — 눈알이 눈꺼풀 뒤로
      // 들어가는 걸 흉내내는 쪽이 이 화풍에 맞는다(자매 리포 deriveHalfEye 와 같은 기하).
      const w = p.x1 - p.x0 + 1, h = p.bot1 - p.top0;
      // 최소 높이를 테두리 두께로 — 1px 까지 누르면 눈 하단의 흰자 한 줄만 남아
      // 감은 눈 자리에 흰 선이 그어진다. 테두리 두께면 그 화풍의 선 색으로 닫힌다.
      const nh = Math.max(p.capMed, h * (1 - lid));
      dst.drawImage(img, p.x0, p.top0, w, h, p.x0, p.bot1 - nh, w, nh);
      return;
    }
    // 점눈은 눌러도 지킬 내부가 없다. 위에서 가리기만 해 획 굵기를 보존한다 —
    // 균일 압축은 실눈 획 6.4px 를 1px 이하로 만들어 화풍을 지우다시피 했다.
    dst.drawImage(img, 0, 0);
    for (let x = p.x0; x <= p.x1; x++) {
      if (p.top[x] < 0 || !p.travel[x]) continue;
      // 정수 — 서브픽셀 리샘플은 가는 선을 프레임마다 깜박이게 한다
      const gone = Math.round(lid * p.travel[x]);
      if (gone > 0) dst.clearRect(x, p.top[x], 1, gone);
    }
  }

  function drawEyes(ctx, parts, drawXY, lid, gaze, manifest) {
    const draw = n => parts[n] && ctx.drawImage(parts[n], 0, 0);
    const pr = manifest.pupilRange || 0;
    const gx = gaze[0] * pr, gy = gaze[1] * pr;
    if (lid <= 0.01) {
      draw("eye_L_open"); draw("eye_R_open");
      drawXY("pupil_L", gx, gy); drawXY("pupil_R", gx, gy);
      return;
    }
    const profs = eyeProfiles(parts);
    for (const [side, eyeName, pupilName] of EYE_SIDES) {
      const eye = parts[eyeName], pupil = parts[pupilName];
      if (!eye) continue;
      const p = profs[side];
      if (!pupil || !p) { occludeEye(ctx, eye, p, lid); continue; }
      // 눈동자는 변형하지 않는다 — 같이 누르면 동공이 타원이 돼 졸린 눈처럼 보인다.
      // Live2D·Character Animator 모두 눈동자를 흰자로 '클리핑'할 뿐 변형하지 않는다.
      // 오프스크린은 눈 주변만 다룬다 — 512² 를 통째로 지우고 되합성하면 실제로 픽셀이
      // 바뀌는 5천여 개를 위해 프레임당 1M px 를 왕복하게 된다.
      const m = Math.ceil(Math.max(Math.abs(gx), Math.abs(gy))) + 2;
      const sx = Math.max(0, p.x0 - m), sy = Math.max(0, p.top0 - m);
      const sw = Math.min(ctx.canvas.width - sx, p.x1 - p.x0 + 1 + 2 * m);
      const sh = Math.min(ctx.canvas.height - sy, p.bot1 - p.top0 + 2 * m);
      const off = _offscreen(ctx.canvas.width, ctx.canvas.height, sx, sy, sw, sh);
      occludeEye(off, eye, p, lid);
      off.globalCompositeOperation = "source-atop";   // 남은 눈 알파 안에만 그려진다
      off.drawImage(pupil, gx, gy);
      off.globalCompositeOperation = "source-over";
      ctx.drawImage(off.canvas, sx, sy, sw, sh, sx, sy, sw, sh);
    }
    // 흰자가 있는 눈만 감은 눈 호를 섞는다. 눈알을 끝까지 눌러도 남는 건 흰자 한 줄이라
    // 그 화풍은 스스로 닫히지 못한다. 점·선 눈은 반대로 자기 획이 곧 닫힌 모습이라
    // 호를 얹으면 이중선이 된다 — 실측으로 실눈은 획(275~291)과 호(282~292)가 겹쳤다.
    const seal = profs.cls === "outline" ? clamp01((lid - 0.7) / 0.3) : 0;
    if (seal > 0.01) {
      ctx.globalAlpha = seal;
      draw("eye_L_closed"); draw("eye_R_closed");
      ctx.globalAlpha = 1;
    }
  }

  // 눈동자 클리핑용 오프스크린 — 매 프레임 새로 만들면 GC 압력이 커서 하나를 돌려쓴다.
  // 지우는 건 넘겨받은 사각형만(캔버스 전체를 지우면 프레임당 262k px 가 낭비된다).
  let _off = null;
  function _offscreen(w, h, sx, sy, sw, sh) {
    if (!_off || _off.canvas.width !== w || _off.canvas.height !== h) {
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      _off = c.getContext("2d");
    }
    _off.clearRect(sx, sy, sw, sh);
    return _off;
  }

  // 눈썹 + 눈 (파츠 스프라이트 기반). drawChar2D 와 puppet.html 이 함께 쓴다 —
  // 한쪽에만 개선이 들어가는 사고를 막으려고 한 곳에 둔다.
  function drawFaceParts(ctx, { parts, manifest, W, blink, gaze }) {
    _partsRef = parts; _ctxRef = ctx;
    const drawXY = (n, dx, dy) => parts[n] && ctx.drawImage(parts[n], dx, dy);
    const bR = manifest.browRange || 0;
    const browUp = -bR * Math.min(1, W("browinnerup") + (W("browouterupleft") + W("browouterupright")) / 2)
                 + bR * 0.8 * (W("browdownleft") + W("browdownright")) / 2;   // 찡그림은 반대로 내림
    // 눈썹 각도 — 위아래 이동만으로는 화남·슬픔이 구분되지 않는다(둘 다 같은 눈썹).
    // 안쪽 끝을 내리면 찡그림(화남), 올리면 팔자(슬픔·무서움)가 된다. 좌우 대칭이라 부호 반전.
    const browTilt = (W("browdownleft") + W("browdownright")) / 2 * BROW_TILT
                   - W("browinnerup") * BROW_TILT * 0.8;
    drawBrow("brow_L", browUp, +browTilt, manifest);
    drawBrow("brow_R", browUp, -browTilt, manifest);

    // 눈 — 이진 교체 대신 연속 눈꺼풀. lid 0=완전히 뜸, 1=완전히 감김.
    // eyeSquint(눈웃음)는 부분적으로 감기게, eyeWide(놀람·무서움)는 음수로 더 뜨게 한다.
    const squint = (W("eyesquintleft") + W("eyesquintright")) / 2;
    const wide = (W("eyewideleft") + W("eyewideright")) / 2;
    const lid = clamp01(Math.max(blink, squint * 0.8) - wide * 0.35);
    drawEyes(ctx, parts, drawXY, lid, gaze, manifest);
  }

  function drawChar2D(ctx, { parts, manifest, W, blink, gaze, warp, clearBg = true, head }) {
    if (!parts.base || !manifest) return false;
    const warpOn = !!(warp && warp.ready);
    ctx.clearRect(0, 0, 512, 512);
    // head 를 주면 캔버스 자체를 변형한다 — CSS 변환이 안 먹는 경로(캔버스를 drawImage 로
    // 합성하는 녹화 등)용. CSS 래퍼를 쓰는 페이지는 head 를 넘기지 말 것(이중 적용).
    if (head) {
      ctx.save();
      ctx.translate(256 + head[0] * HEAD_SHIFT * 5.12, 256 + head[1] * HEAD_SHIFT * 5.12);
      ctx.rotate(head[2]);
      ctx.translate(-256, -256);
    }
    const draw = (n, dy = 0) => parts[n] && ctx.drawImage(parts[n], 0, dy);
    const drawXY = (n, dx, dy) => parts[n] && ctx.drawImage(parts[n], dx, dy);
    _partsRef = parts; _ctxRef = ctx;   // drawBrow 가 쓰는 프레임 지역 참조
    // 워프가 켜져 있으면 base 는 WebGL 레이어가 그리므로 여기선 생략
    if (!warpOn) {
      if (clearBg) { ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, 512, 512); }
      draw("base");
    }
    drawFaceParts(ctx, { parts, manifest, W, blink, gaze });
    const jawDy = warp ? warp.jawOverlayDy(W("jawopen"), warpOn, manifest)
                       : W("jawopen") * (manifest.jawDrop || 8);
    drawVectorMouth(ctx, W, manifest, jawDy);
    if (head) ctx.restore();
    return warpOn;
  }

  // ---------- 3D 헤드 마운트 (mark·claire·RPM 공용) ----------
  // 씬에 GLB 를 얹고 ① 모프 구동 대상 수집 ② 눈알 구체(필요 시) ③ 카메라 프레이밍까지 한 번에.
  // three.js 는 페이지가 importmap 으로 로드하므로 THREE 를 주입받는다(코어는 클래식 스크립트).
  //
  // cfg: { eyeSpheres, headFrac } — eyeSpheres 는 안구 메시가 없는 석고 헤드용,
  //   headFrac 는 모델 높이 중 머리가 차지하는 비율(석고=1 머리만 / RPM≈0.22 반신).
  // 반환: { sceneNode, morphMeshes, eyes } — morphMeshes 는 {inf, map} 배열로,
  //   RPM 처럼 머리·눈·이빨이 각각 모프를 가진 모델도 전부 구동된다(하나만 쓰면 눈·이빨이 따로 논다).
  function mountHead3D(THREE, { group, camera, gltf, cfg, prev }) {
    // 검사를 먼저 — 실패 시 이전 헤드를 건드리지 않아야 호출측의 "실패하면 이전 상태 유지"가 성립한다.
    const sceneNode = gltf.scene;
    const morphMeshes = [];
    sceneNode.traverse(o => {
      if (!o.isMesh || !o.morphTargetInfluences) return;
      o.frustumCulled = false;       // 모프로 변형되면 원래 바운딩을 벗어나 컬링될 수 있다
      morphMeshes.push({ inf: o.morphTargetInfluences,
        map: Object.entries(o.morphTargetDictionary || {}).map(([n, i]) => [norm(n), i]) });
    });
    if (!morphMeshes.length) return null;   // 표정 모프가 없는 모델 — 호출측이 상태 표시

    if (prev) {
      if (prev.sceneNode) group.remove(prev.sceneNode);
      // 눈알 구체는 헤드마다 새로 만드므로 교체 시 GPU 자원을 해제한다(반복 전환 시 누수).
      (prev.eyes || []).forEach(e => {
        group.remove(e);
        e.traverse(o => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
      });
    }
    group.position.set(0, 0, 0);
    group.updateMatrixWorld(true);   // .position 변경을 matrixWorld 에 즉시 반영 —
                                     // 안 하면 setFromObject 가 이전 헤드 오프셋을 물어 bbox 가 어긋난다
    group.add(sceneNode);

    const box = new THREE.Box3().setFromObject(sceneNode);
    const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
    const eyes = cfg.eyeSpheres ? [makeEyeball(THREE, group, 1, box), makeEyeball(THREE, group, -1, box)] : [];
    // 프레이밍: 석고는 전체가 머리 / 반신 모델은 상단 headFrac 만큼을 머리로 보고 맞춘다
    const headH = s.y * cfg.headFrac;
    const focusY = cfg.headFrac === 1 ? c.y : box.max.y - headH * 0.42;
    group.position.set(-c.x, -focusY, -c.z);   // 헤드·눈 함께 recenter
    camera.position.set(0, 0, headH * 2.0); camera.lookAt(0, 0, 0);
    camera.near = headH / 100; camera.far = headH * 100; camera.updateProjectionMatrix();
    return { sceneNode, morphMeshes, eyes };
  }

  // 눈알 구체 — 안구 메시가 없는 석고 헤드(mark·claire)의 소켓을 채운다.
  // 위치·크기는 mark 로 보정한 bbox 분율이라 헤드 크기가 달라도 따라간다.
  function makeEyeball(THREE, group, sign, box) {
    const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
    const k = s.y / 41.5;   // mark 기준 스케일
    const eg = new THREE.Group();
    eg.position.set(c.x + sign * 0.111 * s.x, c.y + 0.151 * s.y, c.z + 0.284 * s.z);
    const mat = (color, roughness) => new THREE.MeshStandardMaterial({ color, roughness });
    const sclera = new THREE.Mesh(new THREE.SphereGeometry(1.15 * k, 32, 16), mat(0xf2f0ee, 0.35));
    const iris = new THREE.Mesh(new THREE.SphereGeometry(0.46 * k, 24, 12), mat(0x44546e, 0.3));
    iris.position.z = 0.78 * k;
    const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.2 * k, 16, 8), mat(0x0c0c0c, 0.4));
    pupil.position.z = 1.0 * k;
    eg.add(sclera, iris, pupil);
    group.add(eg);
    return eg;
  }

  // 모프 구동 — mountHead3D 가 준 morphMeshes 에 채널값을 쓴다. blink 는 별도(자동 깜빡임과 max 결합).
  function applyMorphs(morphMeshes, smooth, blink) {
    for (const { inf, map } of morphMeshes) {
      for (const [k, idx] of map) {
        inf[idx] = k === "eyeblinkleft" || k === "eyeblinkright" ? blink : (smooth[k] || 0);
      }
    }
  }

  // ---------- 아이리스 시선 (478점 랜드마크의 홍채 10점 → [-1..1] 근사) ----------
  // 홍채 중심이 눈꼬리(가로)·눈꺼풀(세로) 기준 어디 있는지의 비율 — 머리 회전에 1차 자체 보정.
  // 눈을 거의 감으면(개방도 < 0.28) null — 호출측이 직전 시선을 유지하게 한다(깜빡임 간섭 차단).
  function irisGaze(lm) {
    if (!lm || lm.length < 478) return null;
    const eye = (iris0, c0, c1, top, bot) => {
      let ix = 0, iy = 0;
      for (let i = iris0; i < iris0 + 5; i++) { ix += lm[i].x / 5; iy += lm[i].y / 5; }
      const halfW = Math.abs(lm[c1].x - lm[c0].x) / 2 || 1e-6;
      const h = Math.abs(lm[bot].y - lm[top].y);
      const cx = (lm[c0].x + lm[c1].x) / 2, cy = (lm[top].y + lm[bot].y) / 2;
      return { gx: (ix - cx) / halfW, gy: (iy - cy) / Math.max(h, 1e-6), open: h / halfW };
    };
    const L = eye(468, 33, 133, 159, 145);    // 영상 기준 왼눈
    const R = eye(473, 362, 263, 386, 374);   // 영상 기준 오른눈
    if ((L.open + R.open) / 2 < 0.28) return null;   // 감김 — 세로 신호 무의미
    return [(L.gx + R.gx) / 2, (L.gy + R.gy) / 2];
  }

  // 머리 회전 부호 — 거울 방향은 렌더러와 무관하게 공통이라 코어에서 한 번만 정한다.
  // yaw·roll 이 음수인 이유: 웹캠 영상은 거울 반전해 쓰므로(분석 패널 scale(-1,1)) 가로 성분은
  // 뒤집어야 사용자가 왼쪽을 향할 때 캐릭터도 화면 왼쪽을 향한다 — 시선 gx 와 같은 규약(아래 gazeX 항).
  // pitch(상하)는 반전과 무관해 그대로. 어긋나 보이면 ?hs=y,p,r 로 실험 후 이 기본값을 고친다.
  const HEAD_SIGN = (() => {
    const def = [-1, 1, -1];
    const v = (new URLSearchParams(location.search).get("hs") || "").split(",").map(Number);
    return def.map((d, i) => (Number.isFinite(v[i]) && v[i] !== 0 ? v[i] : d));
  })();

  // ---------- 웹캠 표정 미러링 (MediaPipe FaceLandmarker 블렌드셰이프 52채널) ----------
  // 브라우저 전용(서버·GPU 추론 불필요, github.io OK). 채널 이름이 ARKit 표준이라 lowercase 로
  // 렌더러 W() 채널과 1:1. 시작 시 30프레임 중립 캘리브레이션 후 상대값만 전이(drawface 정규화).
  // 사용: const mirror = makeMirror({ onStatus }); 렌더 루프에서 mirror.apply(smooth, now) 한 줄.
  // 머리 회전은 산만해서 전이하지 않는다(표정 채널만). gain 기본값은 말하기 수준 벌림 보정 —
  // 페이지별 오버라이드 가능하나 6페이지 실측에서 동일 값이 맞았다.
  function makeMirror({ gain, onStatus } = {}) {
    gain = gain || { jawopen: 1.6, mouthsmileleft: 1.4, mouthsmileright: 1.4 };
    const st = { on: false, w: null, neutral: null, samples: [], gsamples: [], gN: [0, 0], head: null, hN: null, frame: 0 };
    let lm = null, video = null, lastT = -1;
    const say = (msg, err) => onStatus && onStatus(msg, err);

    async function start() {
      if (!lm) {
        say(T().mirrorLoading);
        const mp = await import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.17");
        const vision = await mp.FilesetResolver.forVisionTasks(
          "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.17/wasm");
        // 얼굴 메시 토폴로지는 MediaPipe 가 상수로 들고 있다 — 우리가 삼각분할할 필요가 없다.
        // 미리보기 오버레이(makeMirrorPanel)가 debug().mesh 로 받아 선으로 그린다.
        st.mesh = mp.FaceLandmarker.FACE_LANDMARKS_TESSELATION || null;
        lm = await mp.FaceLandmarker.createFromOptions(vision, {
          baseOptions: { modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task", delegate: "GPU" },
          runningMode: "VIDEO", numFaces: 1, outputFaceBlendshapes: true,
          outputFacialTransformationMatrixes: true,   // 머리 회전(yaw·pitch·roll) 추출용
        });
      }
      video = document.createElement("video");
      video.muted = true;
      video.srcObject = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
      await video.play();
      Object.assign(st, { on: true, w: null, neutral: null, samples: [], gsamples: [], gN: [0, 0], head: null, hN: null, frame: 0 });
      say(T().mirrorCalib);
    }
    function stop() {
      st.on = false; st.w = null;
      video?.srcObject?.getTracks().forEach(t => t.stop());
      video = null;
      say("");
    }
    // 머리 회전: MediaPipe 얼굴 변환행렬(열 우선 4x4) → yaw·pitch·roll(rad).
    // 중립 캘리브레이션 기준 편차만 쓰고, 강한 평활(0.88)+클램프로 산만함을 억제한다.
    function headFromMatrix(m) {
      if (!m || st.samples) { st.head = null; return; }   // 캘리브레이션 중엔 머리 미산출
      // 열 우선(col*4+row): m[8]=R02, m[9]=R12, m[10]=R22, m[1]=R10, m[5]=R11.
      // 표준 Tait-Bryan(Y-X-Z) 추출 — 축 혼선 없이 yaw/pitch/roll 분리.
      const pitch = Math.asin(Math.max(-1, Math.min(1, -m[9])));
      const yaw = Math.atan2(m[8], m[10]);
      const roll = Math.atan2(m[1], m[5]);
      const cur = [yaw, pitch, roll];
      if (!st.hN) st.hN = cur.slice();             // 캘리브 직후 첫 프레임 = 중립 자세
      const rel = cur.map((v, i) => v - st.hN[i]);
      const cl = (v, lim) => Math.max(-lim, Math.min(lim, v));
      const t = [cl(rel[0], 0.5), cl(rel[1], 0.35), cl(rel[2], 0.35)];
      st.head = st.head ? st.head.map((v, i) => 0.88 * v + 0.12 * t[i]) : t;
    }

    const LOOK = ["eyelookoutright", "eyelookinleft", "eyelookoutleft", "eyelookinright",
                  "eyelookdownleft", "eyelookdownright", "eyelookupleft", "eyelookupright"];
    function tick(now) {
      if (!st.on || !video || video.readyState < 2) return;
      if (video.currentTime === lastT) return;   // 새 비디오 프레임에서만 추론
      lastT = video.currentTime;
      st.frame++;                                // 패널이 "새 프레임일 때만" 다시 그리도록
      const res = lm.detectForVideo(video, now);
      const cats = res.faceBlendshapes?.[0]?.categories;
      if (!cats) { st.w = null; st.lm = null; return; }   // 얼굴 놓침 → 개입 중단(자연 복귀)
      st.lm = res.faceLandmarks?.[0] || null;    // 분석 패널(비교군 시각화)용 원본 랜드마크
      headFromMatrix(res.facialTransformationMatrixes?.[0]?.data);
      const raw = {};
      for (const c of cats) raw[c.categoryName.toLowerCase()] = c.score;
      const g = irisGaze(res.faceLandmarks?.[0]);   // 아이리스 정밀 시선 (감김이면 null)
      if (!st.neutral) {                          // 30프레임 평균 = 중립
        st.samples.push(raw);
        if (g) st.gsamples.push(g);
        // 캘리브레이션 중 움직이면 중립이 오염되므로 진행 상황을 안내 (6프레임마다 갱신)
        if (st.samples.length % 6 === 1) say(T().mirrorCalibCount(st.samples.length));
        if (st.samples.length < 30) return;
        const n = {};
        for (const k in raw) n[k] = st.samples.reduce((a, s) => a + (s[k] || 0), 0) / st.samples.length;
        // 시선은 아이리스 합성값으로 대체되므로 eyeLook 채널의 블렌드셰이프 중립은 0으로 —
        // 합성값(이미 게인·클램프 적용)이 표준 파이프라인(중립차감·EMA)을 그대로 통과하게 한다.
        for (const k of LOOK) n[k] = 0;
        st.gN = st.gsamples.length
          ? [st.gsamples.reduce((a, v) => a + v[0], 0) / st.gsamples.length,
             st.gsamples.reduce((a, v) => a + v[1], 0) / st.gsamples.length]
          : [0, 0];
        st.neutral = n;
        st.samples = null; st.gsamples = null;   // 캘리브레이션 끝 — 샘플 버퍼 해제
        say(T().mirrorOn);
        return;
      }
      // 아이리스 시선 → eyeLook 8채널 합성 덮어쓰기 (블렌드셰이프 시선치는 거칠어서 대체).
      // 부호: 거울 느낌 — 사용자가 화면 왼쪽을 보면 캐릭터 눈동자도 화면 왼쪽으로.
      if (g) {
        const cl = v => Math.max(-1, Math.min(1, v));
        const gx = cl(-(g[0] - st.gN[0]) * (gain.gazeX ?? 2.4));
        const gy = cl((g[1] - st.gN[1]) * (gain.gazeY ?? 1.4));   // 세로는 눈꺼풀 가림 탓 신호 약함 → 낮은 게인
        raw.eyelookoutright = raw.eyelookinleft = Math.max(0, gx);
        raw.eyelookoutleft = raw.eyelookinright = Math.max(0, -gx);
        raw.eyelookdownleft = raw.eyelookdownright = Math.max(0, gy);
        raw.eyelookupleft = raw.eyelookupright = Math.max(0, -gy);
      } else {
        for (const k of LOOK) delete raw[k];   // 깜빡임 등 — 직전 시선(EMA) 유지
      }
      const w = st.w || {};
      for (const k in raw) {
        if (k === "_neutral") continue;
        const n = st.neutral[k] || 0;
        const cal = clamp01((raw[k] - n) / Math.max(0.2, 1 - n) * (gain[k] || 1));
        w[k] = 0.55 * (w[k] || 0) + 0.45 * cal;   // EMA 평활
      }
      st.w = w;
    }
    return {
      get on() { return st.on; },
      start, stop,
      // 렌더 루프 한 줄: 추론 tick + smooth 에 max-결합
      apply(smooth, now) {
        tick(now);
        if (st.w) for (const k in st.w) smooth[k] = Math.max(smooth[k] || 0, st.w[k]);
      },
      // 머리 회전 [yaw, pitch, roll] (rad, 중립 대비·평활·클램프·부호적용됨). 미검출/미시작이면 null.
      // 페이지가 켤지 말지만 결정한다 — 축 매핑은 페이지 몫, 부호(거울 방향)는 전 페이지 공통이라 여기서.
      head: () => st.head && st.head.map((v, i) => HEAD_SIGN[i] * v),
      // 분석 패널용: 원본 비디오 + 478점 랜드마크 + 캘리브레이션된 채널값(캐릭터 구동값과 동일)
      debug: () => ({ video, lm: st.lm, w: st.w, frame: st.frame, mesh: st.mesh }),
    };
  }

  // ---------- 상태줄 setter ----------
  function bindStatus(el) {
    return (msg, isError) => { el.textContent = msg; el.className = isError ? "error" : ""; };
  }

  // ---------- 마이크 음성인식 (Web Speech API — 브라우저 내장, 모델 불필요) ----------
  // 미지원 브라우저(Chrome 계열 외)면 null 반환 → 페이지가 마이크 버튼을 숨긴다.
  // onText(텍스트, isFinal): 인식 중간결과(false)와 최종결과(true)를 모두 전달.
  function makeMic({ lang = "ko-KR", onText, onState }) {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return null;
    const rec = new SR();
    rec.lang = lang;
    rec.interimResults = true;   // 말하는 도중에도 텍스트를 보여줌
    rec.continuous = false;      // 한 문장 말하면 자동 종료 (푸시투토크 방식)
    let listening = false;
    rec.onstart = () => { listening = true; onState && onState("listening"); };
    rec.onend = () => { listening = false; onState && onState("idle"); };
    rec.onerror = e => { listening = false; onState && onState("error", e.error); };
    rec.onresult = e => {
      let fin = "", interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) fin += r[0].transcript; else interim += r[0].transcript;
      }
      if (fin.trim()) onText(fin.trim(), true);
      else if (interim) onText(interim, false);
    };
    return {
      toggle() { if (listening) rec.stop(); else { try { rec.start(); } catch (_) {} } },
      start() { if (!listening) { try { rec.start(); } catch (_) {} } },
      stop() { if (listening) rec.stop(); },
    };
  }

  // ---------- 대화 (LLM 응답 — 로컬 서버 전용) ----------
  // 반환: {reply, emotion}. emotion 은 EMOTIONS 키 중 하나(LLM 판단). 실패 시 throw.
  async function chat(text, history, persona) {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, history: history || [], persona: persona || null }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    return res.json();
  }

  // ---------- 대화 모드 글루 (마이크/입력 → LLM → 아바타 응답; puppet·studio3d 공용) ----------
  // history 상태 + 채팅로그(addTurn) + runChat + 모드 토글 + 지우기 + 마이크 배선을 소유.
  // 페이지 주입: speak(text,emotion) 발화 함수, botName(봇 턴 화자명), placeholderOn(대화 모드 안내문),
  //   logEl/chatModeEl/clearBtnEl/textEl/sendEl/micBtnEl/formEl 엘리먼트, statusSet(bindStatus 결과),
  //   getPersona()(선택 — 현재 캐릭터 성격; 없으면 기본 정체성). placeholderOff 는 입력창 초기 placeholder 재사용.
  // 반환 { runChat } — onsubmit 의 chat|say 분기는 페이지가 얇게 소유(runChat 알맹이만 코어).
  function makeChat({ speak, logEl, botName, placeholderOn, chatModeEl, clearBtnEl, textEl, sendEl, micBtnEl, formEl, statusSet, getPersona, audioEl }) {
    const history = [];   // [{role, content}] 최근 턴만 유지
    let handsFree = false, busy = false;   // 연속 대화: 응답 재생이 끝나면 자동 재청취
    const placeholderOff = textEl.placeholder;
    function addTurn(who, text, cls) {
      const div = document.createElement("div");
      div.className = "turn";
      div.innerHTML = `<span class="who">${who}</span><span class="${cls}"></span>`;
      div.lastChild.textContent = text;   // 사용자 입력이므로 textContent로 안전하게
      logEl.appendChild(div);
      logEl.scrollTop = logEl.scrollHeight;
    }
    chatModeEl.onchange = () => {
      const on = chatModeEl.checked;
      logEl.style.display = on ? "grid" : "none";
      clearBtnEl.style.display = on ? "" : "none";
      sendEl.textContent = on ? "말 걸기" : "말하기";
      textEl.placeholder = on ? placeholderOn : placeholderOff;
    };
    clearBtnEl.onclick = () => { history.length = 0; logEl.innerHTML = ""; statusSet(""); };
    async function runChat(text) {
      busy = true;   // 아바타가 생각·발화하는 동안 재청취 금지 (자기 목소리 인식 방지)
      try {
        addTurn("나", text, "me");
        statusSet("생각 중…");
        const { reply, emotion } = await chat(text, history.slice(-6), getPersona && getPersona());   // 최근 3턴 + 캐릭터 성격
        history.push({ role: "user", content: text }, { role: "assistant", content: reply });
        addTurn(botName, reply, "bot");
        statusSet("말하는 중…");
        await speak(reply, emotion);
        // speak 는 재생 시작 시점에 반환 — 연속 대화면 재생이 실제로 끝날 때까지 대기 후 재청취.
        // 주입된 audioEl 만 보면 안 된다 — 영상 경로는 오디오가 mp4 안에 있어 <video> 로 나가고
        // <audio> 는 멈춘 채라 대기가 통째로 스킵됐다(= 영상이 말하는 중에 마이크가 열림).
        // 그래서 지금 실제로 재생 중인 매체를 찾아 그걸 기다린다. audioEl 우선 — 실시간 경로는 종전과 동일.
        const playing = [audioEl, ...document.querySelectorAll("audio, video")]
          .find(el => el && !el.paused && !el.ended);
        // ended 만 기다리면 영구히 걸린다 — 스트리밍 mp4 는 캐릭터 전환(pause)·스트림 끊김(error)으로도
        // 끝난다. 그 경우 busy 가 안 풀려 마이크가 영영 안 열린다. 버퍼링 중엔 waiting 이라 안 깨진다.
        if (handsFree && playing)
          await new Promise(res => ["ended", "pause", "error"]
            .forEach(ev => playing.addEventListener(ev, res, { once: true })));
      } finally {
        busy = false;
        if (handsFree && mic) { statusSet(""); mic.start(); }
      }
    }
    // 마이크 (브라우저 음성인식 — 미지원 브라우저면 makeMic null → 버튼 숨김 유지)
    const mic = makeMic({
      onText: (t, isFinal) => {
        textEl.value = t;
        if (isFinal) formEl.requestSubmit();   // 말이 끝나면 자동 전송
      },
      onState: (st, err) => {
        micBtnEl.classList.toggle("on", st === "listening" || handsFree);
        if (st === "listening") statusSet(handsFree ? "듣고 있어요… (연속 대화 — 마이크 버튼으로 종료)" : "듣고 있어요… 말씀하세요");
        else if (st === "error") {
          if (err === "not-allowed") { handsFree = false; statusSet("마이크 권한이 필요합니다.", true); }
          else statusSet(`음성 인식 오류: ${err}`, true);
        } else if (st === "idle" && handsFree && !busy) {
          // 침묵 타임아웃으로 끊겨도 연속 모드면 잠시 후 재청취 (발화 처리 중이면 runChat 끝에서 재개)
          setTimeout(() => { if (handsFree && !busy) mic.start(); }, 400);
        }
      },
    });
    if (mic) {
      micBtnEl.style.display = "";
      // 마이크 버튼 = 연속 대화 토글: 켜면 듣기→응답→재청취 루프, 다시 누르면 종료
      micBtnEl.onclick = () => {
        if (!chatModeEl.checked) chatModeEl.click();
        handsFree = !handsFree;
        micBtnEl.classList.toggle("on", handsFree);
        if (handsFree) mic.start();
        else { mic.stop(); statusSet(""); }
      };
    }
    return { runChat };
  }

  // ---------- 드래그앤드랍 캐릭터 생성: 어노테이션 캡처 UI (4클릭 상태머신) ----------
  // ① 왼눈 ② 오른눈 ③ 입중심 클릭 → ④ 입 드래그. cv/ctx: 2D 오버레이 캔버스·컨텍스트.
  // needDataUrl: puppet(서버 POST에 b64 필요) true / docs false. onCreate(annot, name, done):
  // 완료 콜백(페이지별 — 서버 POST vs 클라이언트 빌드). 성공 시 done() 호출로 annot 해제(정리 시점 페이지 제어).
  function makeAnnotator({ cv, ctx, setStatus, needDataUrl, onCreate }) {
    let annot = null;  // {img, iw, ih, s, ox, oy, step, eyeL, eyeR, mouthC, box, dragStart, dataUrl}
    const STEPS = ["① 왼쪽 눈을 클릭하세요", "② 오른쪽 눈을 클릭하세요",
                   "③ 입 중심을 클릭하세요", "④ 입 전체를 드래그로 감싸세요"];

    function startAnnot(img, dataUrl) {
      const s = Math.min(512 / img.width, 512 / img.height);
      annot = { img, iw: img.width, ih: img.height, s,
                ox: (512 - img.width * s) / 2, oy: (512 - img.height * s) / 2, step: 0, dataUrl };
      setStatus(STEPS[0] + "  (ESC로 취소)");
    }
    function toOrig(e) {
      const r = cv.getBoundingClientRect();
      return [((e.clientX - r.left) * 512 / r.width - annot.ox) / annot.s,
              ((e.clientY - r.top) * 512 / r.height - annot.oy) / annot.s];
    }
    function draw() {
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, 512, 512);
      ctx.drawImage(annot.img, annot.ox, annot.oy, annot.iw * annot.s, annot.ih * annot.s);
      const dot = (p, color) => {
        if (!p) return;
        ctx.beginPath();
        ctx.arc(annot.ox + p[0] * annot.s, annot.oy + p[1] * annot.s, 6, 0, Math.PI * 2);
        ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.stroke();
      };
      dot(annot.eyeL, "#00a2ff"); dot(annot.eyeR, "#00a2ff"); dot(annot.mouthC, "#ff5b5b");
      if (annot.box) {
        const [x0, y0, x1, y1] = annot.box;
        ctx.strokeStyle = "#ff5b5b"; ctx.lineWidth = 2;
        ctx.strokeRect(annot.ox + x0 * annot.s, annot.oy + y0 * annot.s,
                       (x1 - x0) * annot.s, (y1 - y0) * annot.s);
      }
      // 단계 안내를 그림 위에 얹는다 — 페이지 하단 상태줄에만 띄웠더니 못 보고 지나쳐서,
      // 어노테이션이 끝나지 않은 채(=캐릭터 미생성) 발화를 시도하는 일이 생겼다.
      ctx.fillStyle = "rgba(0,0,0,.72)";
      ctx.fillRect(0, 0, 512, 46);
      ctx.fillStyle = "#fff";
      ctx.font = "600 18px 'Pretendard','Noto Sans KR',sans-serif";
      ctx.textBaseline = "middle";
      ctx.fillText(STEPS[Math.min(annot.step, 3)], 16, 23);
    }
    cv.addEventListener("pointerdown", e => {
      if (!annot) return;
      const p = toOrig(e);
      if (annot.step === 0) { annot.eyeL = p; annot.step = 1; }
      else if (annot.step === 1) { annot.eyeR = p; annot.step = 2; }
      else if (annot.step === 2) { annot.mouthC = p; annot.step = 3; }
      else if (annot.step === 3) { annot.dragStart = p; }
      setStatus(STEPS[Math.min(annot.step, 3)] + "  (ESC로 취소)");
    });
    cv.addEventListener("pointermove", e => {
      if (!annot || annot.step !== 3 || !annot.dragStart) return;
      const p = toOrig(e), s = annot.dragStart;
      annot.box = [Math.min(s[0], p[0]), Math.min(s[1], p[1]), Math.max(s[0], p[0]), Math.max(s[1], p[1])];
    });
    cv.addEventListener("pointerup", async () => {
      if (!annot || annot.step !== 3 || !annot.box) return;
      if (annot.box[2] - annot.box[0] < 5) { annot.dragStart = null; return; }
      const name = prompt("캐릭터 이름:", "내 캐릭터");
      if (name === null) { annot.dragStart = null; annot.box = null; return; }
      await onCreate(annot, name, () => { annot = null; });
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && annot) { annot = null; setStatus(""); }
    });
    function acceptFile(file) {
      if (!file || !file.type.startsWith("image/")) return;
      if (needDataUrl) {   // b64 dataURL 로 읽어 annot.dataUrl 에 보존 (서버 POST용)
        const reader = new FileReader();
        reader.onload = () => {
          const img = new Image();
          img.onload = () => startAnnot(img, reader.result);
          img.src = reader.result;
        };
        reader.readAsDataURL(file);
      } else {             // objectURL 로 바로 로드 (클라이언트 빌드는 dataURL 불필요)
        const img = new Image();
        img.onload = () => startAnnot(img, null);
        img.src = URL.createObjectURL(file);
      }
    }
    return { active: () => !!annot, draw, acceptFile };
  }

  return {
    norm, inferEmotion, classifyEmotion, voiceProsody, smoothStep, weightsFromAnim, shapeAnim, SHAPE,
    EMOTIONS, makeEmotion, makeBlink, makeCursorTracker, makeGaze, makeHeadWander,
    makeMouthPicker, drawVectorMouth, drawSpriteMouth, drawSplitLips, makeWarp, speakFlow, speakWithEmotion,
    bindStatus, makeAnnotator, makeMic, chat, makeChat, makeShowcase, pickReaction, makeMirror, irisGaze, makeMirrorPanel,
    mountHead3D, applyMorphs, drawChar2D, drawFaceParts, setLocale,
    __eyeProfiles: eyeProfiles,   // 콘솔·검증 전용. 공개 계약 아님(반환 형태가 바뀔 수 있다).
  };
})();
