# JoyVASA 패치

`JoyVASA/` 는 외부 저장소라 `.gitignore` 에 있다. 우리가 손댄 부분만 여기 패치로 남긴다 —
재설치·재클론하면 다시 적용해야 한다.

## joyvasa_inject.patch

`src/live_portrait_wmg_pipeline.py` 의 렌더 루프에 주입 지점 두 개를 낸다.

| 스케줄 | 하는 일 | 세팅하는 곳 |
|---|---|---|
| `args.eye_ratio_schedule` | 프레임별 target eyes-open ratio → 강제 깜빡임 | `pipeline.py` `make_blink_schedule` |
| `args.exp_delta_schedule` | 프레임별 `exp`(21×3) 델타 → 감정 표정(눈썹) | `pipeline.py` `emotion_exp_delta` |

기준 커밋은 업스트림 `916a90f`.

```bash
git clone https://github.com/jdh-algo/JoyVASA.git
cd JoyVASA
git checkout 916a90f                              # 기준 커밋 고정 — 이래야 결정적이다
git apply ../patches/joyvasa_inject.patch
```

적용 확인 (face 루트에서):

```bash
grep -q exp_delta_schedule JoyVASA/src/live_portrait_wmg_pipeline.py && \
grep -q eye_ratio_schedule JoyVASA/src/live_portrait_wmg_pipeline.py && echo "patch OK"
```

`grep -c` 로 개수를 세지 말 것 — 매칭 *줄* 수라 주입 하나에 여러 줄이 잡힌다. 예전 README 가
"2 여야 한다"고 적었는데 실제로는 5 였고, **아무도 실행하지 않아 깜빡임 hunk 가 패치에서
빠진 걸 오래 몰랐다**(복원하면 깜빡임이 통째로 사라지는 상태였다).

## 패치를 안 하면 어떻게 되나

**조용히 죽는다.** `ArgumentConfig` 는 frozen 이 아니라 없는 속성을 대입해도 예외가 없고,
렌더 분기가 항등식이라 립싱크는 그대로 나온다 — 표정과 깜빡임만 사라지고 영상은 정상으로
보인다. 그 상태로 인덱스를 측정하면 "어떤 인덱스도 효과 없음" 이라는 거짓 결론까지 나온다.

그래서 `pipeline.py` `_load()` 가 모델을 올리기 **전에** `inspect.getsource` 로 두 심볼을
확인하고 없으면 `RuntimeError` 를 낸다. GPU 비용 0.

## JoyVASA 를 직접 고쳤을 때

반드시 패치를 다시 뽑는다:

```bash
git -C JoyVASA diff -- src/live_portrait_wmg_pipeline.py > patches/joyvasa_inject.patch
```

그리고 **깨끗한 원본에 적용되는지** 확인한다 — 현재 파일에 `git apply --check` 를 하면
"이미 적용됨" 으로 실패하므로 그 검사는 의미가 없다:

```bash
T=$(mktemp -d) && mkdir -p "$T/src"
git -C JoyVASA show HEAD:src/live_portrait_wmg_pipeline.py > "$T/src/live_portrait_wmg_pipeline.py"
(cd "$T" && git init -q . && git apply --check ~/face/patches/joyvasa_inject.patch && echo "적용 가능")
rm -rf "$T"
```

## 표정 인덱스

`exp` 21×3 의 의미는 업스트림에 문서화돼 있지 않아 `tools/exp_index_probe.py` 로 실측한다.
확인된 것:

- **2 (y축) = 눈썹** — 양수가 올림. 국소도 0.60(얼굴도 약간 따라 움직인다). 감정 매핑에 사용 중
- **6·12·14·17·19·20 = 입술** — 소스에 명시. 립싱크가 매 프레임 덮어쓰므로 감정용으로 못 쓴다
- **3·4·11·15 = 얼굴 전체 상하 이동** — 눈썹 대역 변화가 커서 눈썹으로 오인하기 쉽다. 전역이라 쓰면 안 된다

판정은 "어느 대역이 가장 큰가"가 아니라 **"그 대역만 변했는가"**(국소도)로 한다. 그리고
숫자만 보지 말고 렌더를 눈으로 확인한다 — 3·4 를 숫자만 보고 눈썹으로 단정했다가
눈으로 보고서야 전역 이동임을 알았다.
