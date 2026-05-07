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
├── approve.py              # draft → approved 자동 승급 (B안)
├── reject.py               # 특정 슬롯 발행 차단 (rejected.flag)
├── scheduler.py            # Meta Graph API 업로드
├── config.yaml
├── requirements.txt
└── .env.example

.github/workflows/
├── generate-cardnews.yml   # (수동) 일주일치 draft 생성 후 push
├── pages-deploy.yml        # output/** 변경 시 GitHub Pages 자동 배포
├── auto-approve.yml        # KST 06:00 / 15:30 → draft → approved
└── instagram-publish.yml   # KST 08:30 / 18:00 → 인스타 발행
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

### 이미지 호스팅 — GitHub Pages (자동)

Meta는 `image_url`을 직접 fetch 하므로 PNG가 외부 공개 URL이어야 함.
이 레포는 `cardnews-system/output/**` 에 변경이 있으면 GitHub Actions(`pages-deploy.yml`)가
**자동으로 GitHub Pages에 배포**한다.

1. 레포 Settings → Pages → Build and deployment: **GitHub Actions** 선택
2. 첫 푸시 후 Pages URL 확인 (예: `https://<owner>.github.io/<repo>/`)
3. GitHub repo Settings → Secrets and variables → Actions
   - **Variables**: `PUBLIC_MEDIA_BASE_URL = https://<owner>.github.io/<repo>/cardnews`
   - **Secrets**: `META_ACCESS_TOKEN`, `IG_USER_ID` (선택: `NOTIFY_WEBHOOK_URL`)

배포된 PNG의 실제 URL 형태:
```
https://<owner>.github.io/<repo>/cardnews/<YYYY-MM-DD>/<AM|PM>/approved/01.png
```

## 3. 사용법

### 생성

```bash
# 즉시 1세트 테스트(LLM 없이)
python generate.py --topic "거절 처리 화법" --preview --no-llm

# LLM으로 일주일치 × AM/PM = 14세트
python generate.py --days 7 --topic "보험 상담 화법" --slots AM,PM --start 2026-05-08
```

생성물은 `output/YYYY-MM-DD/{AM|PM}/draft/01.png ... 0N.png`. 검수 후 `approved/` 로 이동해야 업로드 큐에 진입함.

### 검수 (B안 — 시간 기반 자동 승인)

생성된 `draft/` PNG를 GitHub Pages 미리보기 또는 로컬에서 확인 후, **그대로 두면 자동 발행**된다.
문제가 있는 슬롯은 `reject.py`로 차단.

```bash
# 5/8 AM 슬롯 발행 차단
python reject.py --date 2026-05-08 --slot AM --reason "보상 화법 섞임"
git add cardnews-system/output && git commit -m "reject 5/8 AM" && git push
```

자동 승인 시점 (GitHub Actions cron, `auto-approve.yml`):
- AM 슬롯: 게시 2.5h 전 = **KST 06:00**
- PM 슬롯: 게시 2.5h 전 = **KST 15:30**

자동 승인은 다음 조건이면 스킵:
- `output/<date>/<slot>/rejected.flag` 존재
- `approved/` 가 이미 차 있음
- `draft/` 가 비어 있음

수동 승인/발행:

```bash
python approve.py  --date 2026-05-08 --slot AM           # draft → approved
python scheduler.py --slot AM --date 2026-05-08 --dry-run
python scheduler.py --slot AM --date 2026-05-08
```

### 자동 발행 (GitHub Actions cron, `instagram-publish.yml`)

- AM: KST 08:30
- PM: KST 18:00

수동 트리거: GitHub repo → Actions → "Publish cardnews to Instagram" → Run workflow (slot 선택, dry_run 옵션).

## 4. 콘텐츠 정책

- **주제 자동 회전**: `config.yaml` 의 `content.topics` 풀(기본 16개)을 슬롯마다 회전하며 사용.
  CLI/워크플로에 `--topic` 을 비워두면 자동 회전, 채우면 단일 토픽 강제.
  주제를 바꾸거나 늘리려면 `config.yaml` 의 `content.topics` 만 편집.
- 블랙리스트: 보험금 청구·심사·약관 해석·보상 분쟁 (사실관계 오류 위험) — `content.exclude_topics`.
- **사인오프**: `config.yaml` 의 `brand.name`, `brand.meta` 를 본인 값으로 반드시 교체. placeholder 그대로면 generate.py 가 경고를 띄움. 소속 GA명 표기 금지.
- 의료자문/약관 인용이 필요한 주제는 사람 검수 후 수동 발행.

## 6. 첫 롤아웃 절차

GitHub Actions의 cron은 **기본적으로 default 브랜치(main)** 에서만 도므로, 이 브랜치를 main에 머지한 뒤 아래 순서로 검증.

1. **레포 설정 (한 번만)**
   - Settings → **Pages** → Source: *GitHub Actions*
   - Settings → Secrets and variables → Actions
     - Secrets: `ANTHROPIC_API_KEY`, `META_ACCESS_TOKEN`, `IG_USER_ID`
     - Variables: `PUBLIC_MEDIA_BASE_URL` = `https://<owner>.github.io/<repo>/cardnews`
2. **첫 생성 (Actions → "Generate cardnews" → Run workflow)**
   - 빠른 검증: `use_llm = false`, `days = 1`, `slots = AM` → 1세트 더미 PNG가 push됨
   - 정상 생성: `use_llm = true`, `days = 7`, `slots = AM,PM`
3. **Pages 배포 확인**: push 직후 "Deploy cardnews to Pages" 워크플로가 돌고, 끝나면 `https://<owner>.github.io/<repo>/cardnews/<date>/<slot>/draft/01.png` 로 미리보기.
4. **검수**: 문제 슬롯만 `python reject.py --date X --slot AM` 후 push. 그대로 두면 게시 2.5h 전 자동 승인.
5. **발행 dry-run**: Actions → "Publish cardnews to Instagram" → Run workflow → `dry_run = true`. 로그에서 빌드된 image_url 들 확인.
6. **실발행 한 번**: 같은 워크플로를 `dry_run = false`로 한 번 → 인스타 피드에 캐러셀 게시 확인.
7. 이후 cron이 매일 KST 06:00/15:30 자동 승인, 08:30/18:00 자동 발행.
