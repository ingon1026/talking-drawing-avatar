/* avatar_core.js — 세 아바타 페이지(static/puppet.html, docs/index.html, static/studio3d.html)의
 * 공유 렌더 코어. 복붙 드리프트 방지를 위해 공통 로직을 여기 한 곳에 모은다.
 *
 * docs/avatar_core.js 는 이 파일의 복사본이다 — 수정 후 반드시
 *     cp static/avatar_core.js docs/
 * 로 동기화할 것. (app.py 기동 시 두 사본 해시를 비교해 불일치를 경고한다.)
 *
 * 일반 <script src> 로 로드되는 전역 스크립트이며 window.AvatarCore 를 정의한다.
 * 소비 페이지보다 먼저 로드할 것. 팩토리 함수들은 정의 시점에 DOM/전역에 접근하지 않고,
 * 페이지가 필요한 엘리먼트·접근자를 인자로 넘겨 호출한다.
 */
window.AvatarCore = (() => {

  // ---------- 내부 유틸 ----------
  const norm = s => s.toLowerCase().replace(/[_\-\s]/g, "");
  const avgLR = (W, base) => (W(base + "left") + W(base + "right")) / 2;   // 좌우 채널 평균
  const roundness = W => Math.max(W("mouthpucker"), W("mouthfunnel"));       // 오므림 세기
  const POLL_MS = 350;                                                       // 잡 폴링 간격
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
  };
  // 부정 표현 — "안 좋아", "좋지 않아", "재미없어"는 기쁨이 아니다.
  const NEGATION = /안\s*(좋|기쁘|행복|즐거|반가)|(좋|기쁘|행복|즐겁)지\s*(않|못)|재미\s*없|별로|싫증/;

  /** 텍스트 → {emo, intensity} | null. 모델 없이 점수 사전으로 추론(정적 데모에서도 동작). */
  function inferEmotion(text) {
    const score = { joy: 0, sad: 0, angry: 0, surprise: 0 };
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

  // ---------- 감정 → 목소리 톤 (얼굴만 웃고 목소리는 무표정한 괴리 해소) ----------
  // 비율값: rate=속도, pitch=음높이, volume=크기. edge-tts("+10%"/"+14Hz")와 브라우저 TTS(배수) 양쪽에서 사용.
  const VOICE_STYLE = {
    joy:      { rate: 0.10, pitch: 0.16 },
    sad:      { rate: -0.12, pitch: -0.12 },
    angry:    { rate: 0.06, pitch: -0.06, volume: 0.15 },
    surprise: { rate: 0.08, pitch: 0.22 },
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

  // ---------- 감정 프리셋 (studio3d 버전이 superset 이라 그것으로 통합) ----------
  const EMOTIONS = {
    neutral: {},
    joy: { mouthsmileleft: 0.55, mouthsmileright: 0.55, cheeksquintleft: 0.45, cheeksquintright: 0.45, eyesquintleft: 0.25, eyesquintright: 0.25 },
    sad: { mouthfrownleft: 0.5, mouthfrownright: 0.5, browinnerup: 0.7, mouthshrugupper: 0.2 },
    angry: { browdownleft: 0.85, browdownright: 0.85, nosesneerleft: 0.4, nosesneerright: 0.4, mouthpressleft: 0.4, mouthpressright: 0.4, jawforward: 0.25 },
    surprise: { browinnerup: 0.6, browouterupleft: 0.75, browouterupright: 0.75, eyewideleft: 0.8, eyewideright: 0.8, jawopen: 0.3 },
  };

  // 감정 상태 + 버튼 배선. buttons/activeColor 는 페이지가 주입(2D #5b8cff / 3D #76b900).
  function makeEmotion(buttons, activeColor) {
    let emotion = EMOTIONS.neutral;
    let sticky = true;   // 수동 버튼=유지, 자동(발화 감정)=발화 끝나면 중립 복귀
    let hold = 1;        // 감정 세기 게이트(0~1). 자동 감정은 유휴 중 감쇠.
    // intensity: 자동 추론 시 감정 세기(0~1)로 프리셋 값을 스케일. 버튼 클릭은 항상 1.
    // 항상 새 객체로 스케일 — 공유 EMOTIONS 프리셋 앨리어싱 회피(v*1===v 라 무손실).
    // isSticky=false(자동 발화 감정)면 발화가 끝난 뒤 표정이 얼어붙지 않고 천천히 풀린다.
    function setEmotion(key, intensity = 1, isSticky = true) {
      const base = EMOTIONS[key] || EMOTIONS.neutral;
      emotion = {};
      for (const k in base) emotion[k] = base[k] * intensity;
      sticky = isSticky; hold = 1;
      buttons.forEach(x => x.style.background = x.dataset.emo === key ? activeColor : "#2a2a35");
    }
    buttons.forEach(b => { b.onclick = () => setEmotion(b.dataset.emo); });   // 버튼은 sticky 기본
    return {
      setEmotion,
      // 감정 프리셋을 현재 평활값에 max-결합. speaking=발화 중이면 유지, 자동 감정은 유휴 시 ~1.5s 감쇠.
      applyMax(smooth, speaking) {
        hold = (sticky || speaking) ? 1 : hold * 0.98;
        for (const k in emotion) smooth[k] = Math.max(smooth[k] || 0, emotion[k] * hold);
      },
    };
  }

  // ---------- 깜빡임 (버튼 + 자동) ----------
  // autoBlink:()=>bool, intervalMs:()=>ms 는 매 프레임 라이브 조회. duration/jitter 상수 페이지별
  // (puppet/docs 140·0.6·슬라이더, studio3d 150·0.8·3500). 상태는 클로저에 캡슐화.
  function makeBlink({ autoBlink, intervalMs, duration, jitter }) {
    let blinkUntil = 0, nextAutoBlink = performance.now() + 4000;
    return {
      trigger() { blinkUntil = performance.now() + duration; },
      value(now) {
        if (autoBlink() && now > nextAutoBlink) {
          blinkUntil = now + duration;
          nextAutoBlink = now + intervalMs() * (0.7 + Math.random() * jitter);  // 자연스러운 지터
        }
        return now < blinkUntil ? 1 : 0;
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
  // cursor: makeCursorTracker 결과, mulX/mulY: 커서 배율(puppet 0.9/0.6, studio3d 0.8/0.5).
  // 반환: (sliderVal, W) => [gx, gy]. docs 는 커서 전용이라 이 팩토리 대신 인라인 유지.
  function makeGaze(cursor, { mulX, mulY }) {
    let gx = 0, gy = 0, sacX = 0, sacY = 0, sacNext = 0;
    return (sliderVal, W) => {
      const chX = (W("eyelookoutright") + W("eyelookinleft") - W("eyelookoutleft") - W("eyelookinright")) / 2;
      const chY = (W("eyelookdownleft") + W("eyelookdownright") - W("eyelookupleft") - W("eyelookupright")) / 2;
      // 슬라이더·엔진채널·커서 다 없으면 유휴 → 눈동자 미세 saccade(죽은 눈 방지)
      const idle = Math.abs(sliderVal) <= 0.01 && !chX && !chY
        && Math.abs(cursor.gx) < 0.02 && Math.abs(cursor.gy) < 0.02;
      if (idle && performance.now() > sacNext) {
        sacNext = performance.now() + 1200 + Math.random() * 2000;
        const center = Math.random() < 0.35;  // 가끔 정면 복귀
        sacX = center ? 0 : (Math.random() - 0.5) * 0.5;
        sacY = center ? 0 : (Math.random() - 0.5) * 0.3;
      }
      const tgtX = Math.abs(sliderVal) > 0.01 ? sliderVal : (chX || (idle ? sacX : cursor.gx * mulX));
      const tgtY = chY || (idle ? sacY : cursor.gy * mulY);
      gx += (tgtX - gx) * 0.15;
      gy += (tgtY - gy) * 0.15;
      return [gx, gy];
    };
  }

  // ---------- 머리 워블 (2D: 발화 끄덕임 nod + 느린 표류 wander + 잔잔한 사인) ----------
  // shakeEl 에 CSS 변환 적용. sway 는 페이지가 넘김(발화 중 1, 아니면 0.5). studio3d 는 3D라 미사용.
  function makeHeadWander() {
    let nod = 0, wanderNext = 0, wanderR = 0, wanderY = 0, wanderGoalR = 0, wanderGoalY = 0;
    return function tick(shakeEl, now, jawopen, sway) {
      const t = now / 1000;
      nod = 0.85 * nod + 0.15 * jawopen;
      if (now > wanderNext) {
        wanderNext = now + 2200 + Math.random() * 2500;
        wanderGoalR = (Math.random() - 0.5) * 0.03;
        wanderGoalY = (Math.random() - 0.5) * 5;
      }
      wanderR += (wanderGoalR - wanderR) * 0.02;
      wanderY += (wanderGoalY - wanderY) * 0.02;
      const breath = Math.sin(t * 1.2) * 2;  // ~5s 주기 호흡 — sway 무관(유휴에도 숨 쉼)
      const rot = Math.sin(t * 0.9) * 0.008 * sway + wanderR + nod * 0.015;
      const dy = Math.sin(t * 1.7) * 1.5 * sway + wanderY + nod * 3 + breath;
      // 머리 흔들림은 두 캔버스(WebGL base + 2D 오버레이)를 함께 감싼 래퍼에 CSS 변환으로 적용.
      // transform-origin=center + translateY(% of height) 조합이 기존 ctx translate/rotate와 수학적으로 동일.
      shakeEl.style.transform = `rotate(${rot.toFixed(5)}rad) translateY(${(dy / 512 * 100).toFixed(4)}%)`;
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

    const openH = Math.max(0, jaw * 58 + lowerDown * 10 - Math.max(pressM * 8, W("mouthclose") * 30));
    const wBase = st.width || 34;
    const halfL = wBase * (1 + 0.45 * W("mouthstretchleft") + 0.3 * smL - 0.5 * round);
    const halfR = wBase * (1 + 0.45 * W("mouthstretchright") + 0.3 * smR - 0.5 * round);
    const cy = mcy0 + jawDy;
    const xL = mcx - halfL, xR = mcx + halfR;
    const yCL = cy - 2 - smL * 12 + frL * 12;   // 입꼬리 좌우 독립 (비대칭 표정)
    const yCR = cy - 2 - smR * 12 + frR * 12;
    const yU = cy - openH * 0.38 - upperUp * 8;
    const yD = cy + openH * 0.62;

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
    if (openH > 7) {  // 윗니
      ctx.fillStyle = st.teeth || "#ffffff";
      ctx.fillRect(xL, yU - 2, xR - xL, Math.min(9, openH * 0.32));
    }
    if (openH > 18) {  // 혀
      ctx.fillStyle = st.tongue || "#d97b7b";
      ctx.beginPath();
      ctx.ellipse(mcx, yD, (xR - xL) * 0.3, openH * 0.28, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
    ctx.stroke(path);
  }

  // ---------- 스프라이트 입 크로스페이드 ----------
  // 반드시 스프라이트 모드 브랜치에서만(프레임당 1회) 호출 — pick() 이 히스테리시스 상태를 전진시킴.
  // 전환 중(fade<1)이고 이전 스프라이트가 존재하면 α로 겹쳐 페이드, 아니면 현재만. jawDy 만큼 세로 이동.
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
        uniform float uJaw, uSmileL, uSmileR, uRound, uFrownL, uFrownR;
        varying vec2 vUv;
        float gk(vec2 p, vec2 c, float s){ vec2 d = p - c; return exp(-dot(d, d) / (2.0 * s * s)); }
        void main() {
          vUv = uv;
          vec2 img = vec2(position.x + 256.0, 256.0 - position.y);   // plane → 이미지 픽셀좌표(y down)
          vec2 disp = vec2(0.0);
          disp += vec2( 0.0, 14.0) * uJaw    * gk(img, uJawC,    55.0);   // 턱 드롭
          disp += vec2(-7.0, -9.0) * uSmileL * gk(img, uCornerL, 32.0);   // 좌 입꼬리 (볼 당김)
          disp += vec2( 7.0, -9.0) * uSmileR * gk(img, uCornerR, 32.0);   // 우 입꼬리
          disp += vec2( 8.0,  0.0) * uRound  * gk(img, uCornerL, 32.0);   // 오므림 (안쪽)
          disp += vec2(-8.0,  0.0) * uRound  * gk(img, uCornerR, 32.0);
          disp += vec2(-3.0,  8.0) * uFrownL * gk(img, uCornerL, 32.0);   // 찡그림 (내림)
          disp += vec2( 3.0,  8.0) * uFrownR * gk(img, uCornerR, 32.0);
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
          this.renderer = new T.WebGLRenderer({ canvas: glCanvas, alpha: true, antialias: false, premultipliedAlpha: false });
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
        this.renderer.render(this.scene, this.camera);
      },
    };
  }

  // ---------- 발화 요청 → 잡 폴링 → 결과 (내부) ----------
  async function speakRT({ text, voice, engine, prosody }) {
    const res = await fetch("/api/speak_rt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice, engine, ...(prosody || {}) }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const { job_id } = await res.json();
    let job;
    while (true) {
      job = await fetch(`/api/jobs/${job_id}`).then(r => r.json());
      if (job.status === "done" || job.status === "error") break;
      await new Promise(r => setTimeout(r, POLL_MS));
    }
    if (job.status === "error") throw new Error(job.error);
    return job.result;
  }

  // ---------- 발화 플로우 (puppet·studio3d 공용) ----------
  // speakRT → anim 조립(head 포함, 2D는 미사용이라 무해) → onAnim(재생 전 반영) → audio.src → play.
  // onAnim 은 play 로딩 창 동안 렌더 루프가 최신 anim 을 보게 하려고 src/play 이전에 호출(원본 순서 유지).
  // 페이지 핸들러는 autoEmo·버튼 disable·에러 setStatus 만 담당.
  async function speakFlow({ text, voice, engine, audioEl, onAnim, prosody }) {
    const r = await speakRT({ text, voice, engine, prosody });
    const anim = { fps: r.fps, frames: r.frames, head: r.head, index: r.names.map((n, i) => [norm(n), i]) };
    if (onAnim) onAnim(anim);
    audioEl.src = r.audio_url;
    await audioEl.play();
    return anim;
  }

  // ---------- 감정 결정 + 발화 (puppet·studio3d 공용) ----------
  // emotion 지정(LLM 판단) 있으면 그대로, 없으면 텍스트에서 추론. autoEmo(호출 시점 boolean) 켜져 있으면
  // emo(makeEmotion 인스턴스) 프리셋 + 목소리 톤 적용 후 speakFlow. voice/engine 은 호출 시점 값.
  async function speakWithEmotion({ text, emotion, autoEmo, emo, voice, engine, audioEl, onAnim }) {
    const r = emotion ? { emo: emotion, intensity: 0.9 } : inferEmotion(text);
    let prosody = null;
    if (r && autoEmo) {
      emo.setEmotion(r.emo, r.intensity, false);   // 자동 감정 — 발화 끝나면 중립 복귀
      prosody = voiceProsody(r.emo, r.intensity);
    }
    return speakFlow({ text, voice, engine, audioEl, onAnim, prosody });
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
  function makeChat({ speak, logEl, botName, placeholderOn, chatModeEl, clearBtnEl, textEl, sendEl, micBtnEl, formEl, statusSet, getPersona }) {
    const history = [];   // [{role, content}] 최근 턴만 유지
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
      addTurn("나", text, "me");
      statusSet("생각 중…");
      const { reply, emotion } = await chat(text, history.slice(-6), getPersona && getPersona());   // 최근 3턴 + 캐릭터 성격
      history.push({ role: "user", content: text }, { role: "assistant", content: reply });
      addTurn(botName, reply, "bot");
      statusSet("말하는 중…");
      await speak(reply, emotion);
    }
    // 마이크 (브라우저 음성인식 — 미지원 브라우저면 makeMic null → 버튼 숨김 유지)
    const mic = makeMic({
      onText: (t, isFinal) => {
        textEl.value = t;
        if (isFinal) formEl.requestSubmit();   // 말이 끝나면 자동 전송
      },
      onState: (st, err) => {
        micBtnEl.classList.toggle("on", st === "listening");
        if (st === "listening") statusSet("듣고 있어요… 말씀하세요");
        else if (st === "error") statusSet(err === "not-allowed" ? "마이크 권한이 필요합니다." : `음성 인식 오류: ${err}`, true);
      },
    });
    if (mic) {
      micBtnEl.style.display = "";
      micBtnEl.onclick = () => { if (!chatModeEl.checked) chatModeEl.click(); mic.toggle(); };
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
    norm, inferEmotion, voiceProsody, smoothStep, weightsFromAnim,
    EMOTIONS, makeEmotion, makeBlink, makeCursorTracker, makeGaze, makeHeadWander,
    makeMouthPicker, drawVectorMouth, drawSpriteMouth, makeWarp, speakFlow, speakWithEmotion,
    bindStatus, makeAnnotator, makeMic, chat, makeChat,
  };
})();
