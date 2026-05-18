# BGM 트랙 — mood 기반 자동 매칭

릴스 publish 시 `caption_to_spec` 가 LLM 으로 분류한 mood 값에 따라
이 폴더의 `track_{mood}.mp3` 가 자동 선택돼 영상에 합성됩니다.

## 파일명 규약

각 트랙은 정확히 다음 이름이어야 합니다:

| 파일 | mood | 어울리는 콘텐츠 톤 |
|---|---|---|
| `track_gentle.mp3`    | gentle    | 공감/따뜻 ("같이 고민해보자") |
| `track_calm.mp3`      | calm      | 정보 전달 (담담한 설명) |
| `track_serious.mp3`   | serious   | 페인포인트/위기감 ("이거 놓치면 큰일") |
| `track_uplifting.mp3` | uplifting | 희망/긍정 (성공 사례, 변화) |
| `track_focused.mp3`   | focused   | 전문가 톤 (분석, 데이터) |
| `track_warm.mp3`      | warm      | 인간미 (현장 이야기) — **기본 fallback** |
| `track_subtle.mp3`    | subtle    | 배경용 (잔잔한 팁 모음) |

## 현재 상태: ffmpeg 합성 placeholder

샌드박스 환경에서 외부 음원 호스트 접근이 차단돼서, `_generate_placeholders.sh`
가 ffmpeg sine wave 코드 합성으로 임시 톤 7개를 만들어 둔 상태입니다.
sustain 된 단순 sine 화음이라 진짜 음악이 아닙니다.

## 실제 음원으로 교체하는 법

1. **Pixabay Music** (https://pixabay.com/music/) — CC0, 가입 불필요. 추천 검색어:
   - gentle: `gentle piano`, `soft acoustic`, `lullaby`
   - calm: `calm ambient`, `peaceful background`
   - serious: `cinematic suspense`, `dark ambient piano`
   - uplifting: `uplifting acoustic`, `inspiring corporate`
   - focused: `minimal ambient`, `study lofi`
   - warm: `warm acoustic`, `folk instrumental`
   - subtle: `ambient pad`, `meditation`

2. 각 트랙 **약 15초** (12~18초). 길어도 OK — `mux_bgm` 이 영상 길이로 자름.

3. 다운로드한 mp3 를 **동일한 파일명**으로 이 폴더에 덮어쓰기.
   필요하면 ffmpeg 로 잘라서 길이 조정:
   ```
   ffmpeg -i input.mp3 -t 15 -af "afade=t=in:st=0:d=1,afade=t=out:st=14:d=1" \
          -c:a libmp3lame -b:a 128k track_gentle.mp3
   ```

4. commit + push. 다음 publish 부터 자동으로 새 트랙 적용.

## 매칭 동작 (요약)

```
caption (cardnews 가 생성)
  ↓ caption_to_spec.py (LLM)
mood: "serious"
  ↓ make_reel.py 가 mood_AM.txt 사이드카 저장
  ↓ publish_reel.py 가 사이드카 읽음
track_serious.mp3 선택 → ffmpeg amix → final.mp4 → IG
```

mood 가 7종 중 어느 것에도 매칭 안 되면 fallback 순서: warm → calm → gentle → 첫 트랙.
