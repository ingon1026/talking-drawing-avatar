# JoyVASA 패치

`JoyVASA/` 는 외부 저장소라 `.gitignore` 에 있다. 우리가 손댄 부분만 여기 패치로 남긴다 —
재설치·재클론하면 아래를 다시 적용해야 한다.

## joyvasa_exp_delta.patch

표정 벡터(`exp` 21×3) 주입 경로. `args.exp_delta_schedule[i]` 를 `delta_new` 에 더한다.
감정(눈썹 각도 등)을 생성 단계에서 넣기 위한 것 — 렌더된 영상 위에 스프라이트를 얹으면
머리가 움직일 때 정합이 깨진다.

```bash
cd JoyVASA && git apply ../patches/joyvasa_exp_delta.patch
```

적용 확인:

```bash
grep -c exp_delta_schedule JoyVASA/src/live_portrait_wmg_pipeline.py   # 2 여야 한다
```

인덱스 의미는 `tools/exp_index_probe.py` 로 실측한다(문서화돼 있지 않다).
현재까지: **3·4 = 눈썹**, **6·12·14·17·19·20 = 입술**(소스에 명시).
