"""
발행된 media_id ↔ DM 키워드 매핑을 Cloudflare KV 에 등록.

ig-webhook/ 의 Cloudflare Worker 가 새 댓글 웹훅을 받으면 이 KV 에서
media_id 로 키워드를 조회해서, 댓글 텍스트에 키워드가 포함돼 있으면
자동으로 private reply(DM)를 보낸다.

scheduler.py(캐러셀)/reels/publish_reel.py(릴스) 가 발행 성공 직후
register_dm_keyword() 를 호출한다. 이 등록은 best-effort — 실패해도 이미
끝난 IG 발행 자체를 실패로 되돌리지 않는다 (댓글 자동응답 하나 안 되는 것과
발행 자체가 실패하는 건 심각도가 다르다).

필요 secrets (GitHub Secrets / .env):
  CF_ACCOUNT_ID       — Cloudflare 계정 ID
  CF_KV_NAMESPACE_ID  — ig-webhook/wrangler.toml 에서 만든 KV 네임스페이스 ID
  CF_API_TOKEN        — Workers KV Storage:Edit 권한의 Cloudflare API 토큰
"""
from __future__ import annotations

import json
import os

import requests


def register_dm_keyword(media_id: str, keyword: str, topic: str = "") -> bool:
    """media_id ↔ {keyword, topic} 를 Cloudflare KV 에 등록. 성공 여부 반환."""
    if not keyword:
        return False

    account_id = os.environ.get("CF_ACCOUNT_ID")
    namespace_id = os.environ.get("CF_KV_NAMESPACE_ID")
    token = os.environ.get("CF_API_TOKEN")
    if not (account_id and namespace_id and token):
        print("[dm-registry] CF_ACCOUNT_ID/CF_KV_NAMESPACE_ID/CF_API_TOKEN 미설정 "
              "— 댓글 자동응답 키워드 등록 skip (발행 자체는 정상)")
        return False

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/storage/kv/namespaces/{namespace_id}/values/{media_id}"
    )
    body = json.dumps({"keyword": keyword, "topic": topic}, ensure_ascii=False)
    try:
        r = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=body.encode("utf-8"),
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[dm-registry] KV 등록 실패 (네트워크): {e}")
        return False

    if not r.ok:
        print(f"[dm-registry] KV 등록 실패: HTTP {r.status_code} {r.text[:300]}")
        return False

    print(f"[dm-registry] media_id={media_id} keyword={keyword!r} 등록 완료")
    return True


def read_keyword_sidecar(captions_dir, day, slot) -> dict | None:
    """generate.py 의 write_dm_keyword() 가 남긴 사이드카 파일을 읽는다."""
    from pathlib import Path
    p = Path(captions_dir) / f"{day.isoformat()}_{slot}.keyword.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
