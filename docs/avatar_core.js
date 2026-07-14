/* avatar_core.js — 세 아바타 페이지(static/puppet.html, docs/index.html, static/studio3d.html)의
 * 공유 렌더 코어. 복붙 드리프트 방지를 위해 공통 로직을 여기 한 곳에 모은다.
 *
 * docs/avatar_core.js 는 이 파일의 복사본이다 — 수정 후 반드시
 *     cp static/avatar_core.js docs/
 * 로 동기화할 것. (three.module.js 를 static/vendor·docs/vendor 양쪽에 두는 것과 같은 선례)
 *
 * 일반 <script src> 로 로드되는 전역 스크립트이며 window.AvatarCore 를 정의한다.
 * 소비 페이지보다 먼저 로드할 것. 팩토리 함수들은 정의 시점에 DOM/전역에 접근하지 않고,
 * 페이지가 필요한 엘리먼트·접근자를 인자로 넘겨 호출한다.
 */
window.AvatarCore = (() => {

  // ---------- 순수 유틸 ----------
  const norm = s => s.toLowerCase().replace(/[_\-\s]/g, "");

  // 텍스트 감정 추론 (발화 시 자동 프리셋 — 세 페이지 동일 규칙)
  function inferEmotion(text) {
    if (/ㅋㅋ|ㅎㅎ|하하|호호|웃겨|웃음/.test(text)) return "joy";  // 웃음이 최우선 신호
    if (/[ㅠㅜ]{2,}|슬프|슬퍼|우울|눈물|아파|힘들|속상|외로/.test(text)) return "sad";
    if (/화나|화가|짜증|열받|분노|싫어|그만해/.test(text)) return "angry";
    if (/헉|깜짝|놀라|대박|세상에|믿을 수|[?!]{2,}/.test(text)) return "surprise";
    if (/ㅋㅋ|ㅎㅎ|하하|호호|신나|행복|좋아|좋다|최고|사랑|기뻐|반가|!/.test(text)) return "joy";
    return null;
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

  // 감정 상태 + #emotions 버튼 배선. activeColor 페이지별(2D #5b8cff / 3D #76b900).
  function makeEmotion(activeColor) {
    let emotion = EMOTIONS.neutral;
    function setEmotion(key) {
      emotion = EMOTIONS[key] || EMOTIONS.neutral;
      document.querySelectorAll("#emotions button").forEach(x =>
        x.style.background = x.dataset.emo === key ? activeColor : "#2a2a35");
    }
    document.querySelectorAll("#emotions button").forEach(b => {
      b.onclick = () => setEmotion(b.dataset.emo);
    });
    return { setEmotion, current: () => emotion };
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
  // el 의 pointermove/leave → {gx, gy} (-1..1). 프레임별 평활·엔진채널 결합은 페이지 인라인(상이).
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
      const rot = Math.sin(t * 0.9) * 0.008 * sway + wanderR + nod * 0.015;
      const dy = Math.sin(t * 1.7) * 1.5 * sway + wanderY + nod * 3;
      // 머리 흔들림은 두 캔버스(WebGL base + 2D 오버레이)를 함께 감싼 래퍼에 CSS 변환으로 적용.
      // transform-origin=center + translateY(% of height) 조합이 기존 ctx translate/rotate와 수학적으로 동일.
      shakeEl.style.transform = `rotate(${rot.toFixed(5)}rad) translateY(${(dy / 512 * 100).toFixed(4)}%)`;
    };
  }

  // ---------- 스프라이트 입모양 선택기 (개방도 우선 + 히스테리시스) ----------
  // targetMouth 는 puppet superset — docs 발화경로에서 mouthpress·mouthstretch=0 이라 정확히 환원된다.
  // 크로스페이드 렌더는 페이지별(draw 함수 상이)이라 상태(prevMouth/switchAt/FADE_MS)만 노출.
  function makeMouthPicker() {
    let curMouth = "closed", prevMouth = null, switchAt = 0, mouthCand = "closed", candSince = 0;
    const FADE_MS = 90;
    function targetMouth(W) {
      const jaw = W("jawopen");
      const round = Math.max(W("mouthpucker"), W("mouthfunnel"));
      const wide = Math.max((W("mouthsmileleft") + W("mouthsmileright")) / 2,
                            (W("mouthstretchleft") + W("mouthstretchright")) / 2);
      const press = (W("mouthpressleft") + W("mouthpressright")) / 2;
      if (jaw < 0.06) return (press > 0.2 || W("mouthclose") > 0.25) ? "M" : "closed";
      if (round > wide + 0.08) return jaw > 0.28 ? "O" : "U";
      if (jaw > 0.42) return "A";
      if (wide > 0.22) return jaw < 0.16 ? "I" : "E";
      return jaw < 0.14 ? "closed" : "E";
    }
    return {
      FADE_MS,
      pick(now, W) {
        const t = targetMouth(W);
        if (t !== mouthCand) { mouthCand = t; candSince = now; }
        if (mouthCand !== curMouth && now - candSince >= 70) {  // 70ms 유지 시에만 전환
          prevMouth = curMouth; switchAt = now; curMouth = mouthCand;
        }
        return curMouth;
      },
      get prevMouth() { return prevMouth; },
      get switchAt() { return switchAt; },
    };
  }

  // ---------- 벡터 입 (근육 채널 → 윤곽 제어점 연속 변형) ----------
  // puppet 의 superset 공식으로 통합. 두 2D 페이지에서 갈렸던 두 지점을 회귀 없이 흡수:
  //  · 닫힘곡선 제어점 압력 = max(근육 press, mouthclose*0.5) — puppet(press 위주)·docs(mouthclose 위주)
  //    양쪽의 기존 값을 그대로 재현하며, openH 폐합 항이 이미 쓰는 max(press, close) 패턴과 일관.
  //  · 입꼬리 frown 반영 — puppet 에만 있던 항. docs 슬픔 감정이 세팅해 두고도 버리던 채널을 살리는 개선.
  function drawVectorMouth(ctx, W, manifest, jawDy) {
    const st = manifest.mouthStyle || {};
    const [mcx, mcy0] = manifest.mouthCenter || [256, 340];
    const jaw = W("jawopen");
    const round = Math.max(W("mouthpucker"), W("mouthfunnel"));
    const pressM = (W("mouthpressleft") + W("mouthpressright")) / 2;   // 근육 압력 (openH 폐합용)
    const upperUp = (W("mouthupperupleft") + W("mouthupperupright")) / 2;
    const lowerDown = (W("mouthlowerdownleft") + W("mouthlowerdownright")) / 2;
    const smL = W("mouthsmileleft"), smR = W("mouthsmileright");
    const frL = W("mouthfrownleft"), frR = W("mouthfrownright");
    // 닫힘곡선 제어점 압력: 근육 press 와 mouthclose 유래 압력 중 강한 쪽 (하이브리드 — puppet·docs 양쪽 회귀 0)
    const pressCurve = Math.max(pressM, W("mouthclose") * 0.5);

    // 폐합은 press(근육)·mouthclose(폐쇄) 중 강한 쪽
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
        u.uRound.value = Math.max(W("mouthpucker"), W("mouthfunnel"));
        u.uFrownL.value = W("mouthfrownleft");
        u.uFrownR.value = W("mouthfrownright");
        this.renderer.render(this.scene, this.camera);
      },
    };
  }

  // ---------- 발화 요청 → 잡 폴링 → 결과 ----------
  // puppet·studio3d 공용(docs 제외). pollMs 페이지별(400 / 300). result({audio_url,fps,names,frames,head?})
  // 반환; anim 조립·audio 재생·감정 세팅은 페이지가 담당.
  async function speakRT({ text, voice, engine, pollMs }) {
    const res = await fetch("/api/speak_rt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice, engine }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const { job_id } = await res.json();
    let job;
    while (true) {
      job = await fetch(`/api/jobs/${job_id}`).then(r => r.json());
      if (job.status === "done" || job.status === "error") break;
      await new Promise(r => setTimeout(r, pollMs));
    }
    if (job.status === "error") throw new Error(job.error);
    return job.result;
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
    norm, inferEmotion, smoothStep, weightsFromAnim,
    EMOTIONS, makeEmotion, makeBlink, makeCursorTracker, makeHeadWander,
    makeMouthPicker, drawVectorMouth, makeWarp, speakRT, makeAnnotator,
  };
})();
