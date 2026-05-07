# 보험 상담 화법 카드뉴스 자동화

보험설계사 인스타 피드용 카드뉴스를 자동 생성하고, 정해진 시간(오전 8:30 / 오후 6:00)에 자동 업로드하는 파이프라인.

> 보상(보험금 청구·심사·약관 분쟁) 주제는 사실관계 오류 우려로 자동 생성 대상에서 제외됨.

## 1. 구조

```
cardnews-system/
├── input/                  # 사용자가 던지는 PDF/링크 등 참고 자료
├── templates/
│   └── card.html           # 1080x1080 카드 템플릿 (bottom-stack 적용)
├── output/
│   └── YYYY-MM-DD/
│       ├── AM/
│       │   ├── draft/      # 1차 생성 PNG (검수 대기)
│       │   └── approved/   # 검수 통과한 PNG (업로드 큐)
│       └── PM/
├── captions/               # YYYY-MM-DD_AM.txt / _PM.txt
├── generate.py             # LLM + Playwright 렌더
├── scheduler.py            # Meta Graph API 업로드
├── config.yaml
├── requirements.txt
└── .env.example
```

## 2. 셋업

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env       # 토큰 채우기
```

### Meta 토큰 발급 (수동 1회)

1. 인스타 프로페셔널(비즈니스) 계정 → 페이스북 페이지 연동
2. https://developers.facebook.com → 앱 생성 → "Instagram Graph API" 추가
3. 권한: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`
4. Graph API Explorer에서 단기 토큰 → **장기 토큰(60일)** 으로 교환
5. `IG_USER_ID` 조회: `GET /me/accounts` → page_id → `GET /{page_id}?fields=instagram_business_account`
6. `.env`의 `META_ACCESS_TOKEN`, `IG_USER_ID` 채우기

### 이미지 호스팅

Meta는 `image_url`을 직접 fetch 하므로 PNG가 외부 공개 URL이어야 함.
간단한 옵션:
- GitHub Pages: `output/` 을 별도 public repo의 `docs/`에 푸시 → `PUBLIC_MEDIA_BASE_URL=https://USER.github.io/REPO`
- S3 / Cloudflare R2 / Netlify Drop 도 가능

## 3. 사용법

### 생성

```bash
# 즉시 1세트 테스트(LLM 없이)
python generate.py --topic "거절 처리 화법" --preview --no-llm

# LLM으로 일주일치 × AM/PM = 14세트
python generate.py --days 7 --topic "보험 상담 화법" --slots AM,PM --start 2026-05-08
```

생성물은 `output/YYYY-MM-DD/{AM|PM}/draft/01.png ... 0N.png`. 검수 후 `approved/` 로 이동해야 업로드 큐에 진입함.

### 검수 → 승인

```bash
# 예: 5/8 AM 슬롯 승인
mkdir -p output/2026-05-08/AM/approved
mv output/2026-05-08/AM/draft/*.png output/2026-05-08/AM/approved/
```

### 업로드

```bash
# 수동 한 번
python scheduler.py --slot AM --date 2026-05-08

# dry-run (실제 업로드 없이 URL만 확인)
python scheduler.py --slot AM --dry-run
```

### 스케줄 등록 (cron)

```cron
30 8 * * *  cd /path/to/cardnews-system && /usr/bin/python scheduler.py --slot AM
 0 18 * * * cd /path/to/cardnews-system && /usr/bin/python scheduler.py --slot PM
```

GitHub Actions로 돌리려면 `schedule: cron: '30 23 * * *'` (KST 08:30 = UTC 23:30 전날) 형태로 등록.

## 4. 콘텐츠 정책

- 주제 화이트리스트: 거절 처리, 니즈 환기, 클로징, 가족 동반 상담, DB콜 첫 30초, 추천 요청 화법, 재무주치의 포지셔닝 등 **상담 화법** 한정
- 블랙리스트: 보험금 청구·심사·약관 해석·보상 분쟁 (사실관계 오류 위험)
- 사인오프: 본인 명의 또는 개인 브랜드명만 사용. **소속 GA명 표기 금지.**
- 의료자문/약관 인용이 필요한 주제는 사람 검수 후 수동 발행.

## 5. 디자인 스펙 (고정)

- 1080×1080 PNG, 카드 8~10장/세트
- Navy `#0A1628` / Cream `#F5F1E8` / Gold `#C8A14B` / Red `#B8321A`
- 하단 고정: `.bottom-stack { position: absolute; bottom: 74px; display: flex; flex-direction: column; gap: 16px; }`
