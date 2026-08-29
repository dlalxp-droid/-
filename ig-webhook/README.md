# ig-webhook — 인스타 댓글/DM 자동응답

카드뉴스 cta 카드의 "'키워드' 댓글 또는 DM 주세요" 를 실제로 작동시키는
Cloudflare Worker. 새 댓글이 키워드를 포함하면 그 사람에게 자동으로 private
reply(=DM)를 보내고, 사람이 DM 을 직접 보내도 자동 응답한다.

```
카드뉴스 발행 (scheduler.py/publish_reel.py)
   → media_id ↔ {keyword, topic} 를 Cloudflare KV 에 등록
새 댓글/DM 발생
   → Meta 가 이 Worker 로 webhook POST
   → 댓글이면: media_id 로 KV 조회 → 키워드 포함 시 private reply 발송
   → DM 이면: 웰컴 메시지로 자동 응답 (echo 는 무시해서 무한루프 방지)
```

## 사전 준비 (한 번만)

이미 Meta 앱에 `instagram_manage_comments`, `instagram_manage_messages`
Advanced Access 가 있다는 전제. 없으면 Meta 개발자 콘솔에서 앱 심사부터
필요 — 이 Worker 배포와는 별개로 먼저 해결해야 함.

## 배포 절차

1. **Cloudflare 계정 준비** (없으면 무료로 생성 — cloudflare.com)

2. **Wrangler 로그인**
   ```
   cd ig-webhook
   npm install
   npx wrangler login
   ```

3. **KV 네임스페이스 생성**
   ```
   npx wrangler kv:namespace create DM_KEYWORDS
   ```
   출력된 `id` 값을 `wrangler.toml` 의 `[[kv_namespaces]] id = "..."` 에 채운다.

4. **`wrangler.toml` 의 `[vars]` 채우기**
   - `IG_USER_ID`: 지금 GitHub Secrets 에 있는 것과 같은 값
   - `COMMENT_REPLY_TEMPLATE` / `DM_WELCOME_TEMPLATE`: 원하는 문구로 수정 가능
     (`{topic}` 은 카드뉴스 토픽으로 자동 치환됨)

5. **secret 등록** (wrangler.toml 에 쓰면 안 되는 값들)
   ```
   npx wrangler secret put META_ACCESS_TOKEN
   ```
   → 지금 GitHub Secrets 의 `META_ACCESS_TOKEN` 과 동일한 값 붙여넣기
   ```
   npx wrangler secret put META_APP_SECRET
   ```
   → Meta 앱 대시보드 → 설정 → 기본 → "앱 시크릿"
   ```
   npx wrangler secret put WEBHOOK_VERIFY_TOKEN
   ```
   → 아무 임의의 긴 문자열 직접 정해서 입력 (예: openssl rand -hex 20). 다음
     단계에서 Meta 웹훅 등록 시 같은 값을 입력해야 함.

6. **배포**
   ```
   npx wrangler deploy
   ```
   배포 완료 후 나오는 URL (예: `https://ig-webhook.<your-subdomain>.workers.dev`)
   을 기록해둔다.

7. **Meta 앱에 웹훅 등록** (developers.facebook.com → 해당 앱 → Webhooks)
   - Callback URL: 위에서 나온 Worker URL
   - Verify Token: 6번에서 등록한 `WEBHOOK_VERIFY_TOKEN` 값과 정확히 동일하게
   - 구독 대상: Instagram 제품 아래 `comments`, `messages` 필드 체크
   - "확인 및 저장" 시 Meta 가 Worker 에 GET 검증 요청을 보낸다 — 위 배포가
     끝나 있어야 통과함

8. **카드뉴스 쪽 GitHub Secrets 추가** (repo → Settings → Secrets and variables
   → Actions) — scheduler.py/publish_reel.py 가 발행 직후 이 Worker 의 KV 에
   media_id ↔ keyword 를 등록하기 위해 필요:
   - `CF_ACCOUNT_ID` — Cloudflare 대시보드 오른쪽 사이드바에서 확인
   - `CF_KV_NAMESPACE_ID` — 3번에서 만든 네임스페이스 id (wrangler.toml 에
     넣은 값과 동일)
   - `CF_API_TOKEN` — Cloudflare 대시보드 → My Profile → API Tokens →
     Create Token → "Edit Cloudflare Workers" 템플릿 사용 (또는 커스텀으로
     Account.Workers KV Storage:Edit 권한만) 발급

## 테스트

- 배포 직후: `curl "https://<worker-url>/?hub.mode=subscribe&hub.verify_token=<WEBHOOK_VERIFY_TOKEN>&hub.challenge=123"` → `123` 이 그대로 응답되면 정상
- 실시간 로그: `npx wrangler tail` 실행해두고, 실제 인스타에서 최근 게시물에
  등록된 키워드로 댓글을 달아보면 로그에 처리 과정이 찍힌다
- media_id ↔ keyword 매핑이 KV 에 잘 등록됐는지: 다음 카드뉴스 발행 워크플로
  로그에서 `[dm-registry] media_id=... keyword=... 등록 완료` 확인

## 주의

- Meta 웹훅 페이로드 형태는 API 버전/설정에 따라 조금씩 다를 수 있다.
  `wrangler tail` 로 실제 페이로드를 보면서 `src/index.js` 의 `handleComment`
  / `handleMessage` 파싱 로직을 필요시 조정할 것 (처음 배포 후 실제 댓글/DM
  으로 1회 검증 필수 — 코드만으로 100% 보장 못 함).
- `META_ACCESS_TOKEN` 이 만료/재발급되면 GitHub Secrets 뿐 아니라 여기
  `wrangler secret put META_ACCESS_TOKEN` 도 같이 갱신해야 한다 (두 곳에
  독립적으로 저장돼 있음).
- 공개 웹훅 엔드포인트라 누구나 URL 로 요청을 보낼 수 있다 — `X-Hub-Signature-256`
  서명 검증(`META_APP_SECRET` 기반)으로 Meta 가 보낸 요청만 처리하도록 이미
  막아뒀다. 이 검증 로직을 임의로 끄지 말 것.
