"""
인스타그램 자동 업로드 스케줄러 (Meta Graph API).

흐름:
  1) /output/YYYY-MM-DD/{slot}/approved/  의 PNG들을 모은다
     (검수 후 draft → approved 로 이동된 것만 큐 진입)
  2) 각 PNG의 PUBLIC_MEDIA_BASE_URL 기준 공개 URL을 만든다
     (Meta는 image_url 을 직접 fetch 하므로 외부 접근 가능 URL 필수)
  3) /{IG_USER_ID}/media 로 children 컨테이너 N개 생성 (is_carousel_item=true)
  4) /{IG_USER_ID}/media 로 캐러셀 컨테이너 1개 생성 (media_type=CAROUSEL, children=...)
  5) /{IG_USER_ID}/media_publish 로 publish

cron 등록 예:
   0 9  * * * /usr/bin/python /cardnews-system/scheduler.py --slot AM
   0 13 * * * /usr/bin/python /cardnews-system/scheduler.py --slot NOON
   0 17 * * * /usr/bin/python /cardnews-system/scheduler.py --slot PM
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

import cloudinary
import cloudinary.uploader
import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GRAPH = "https://graph.facebook.com/v21.0"


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def _verify_recent_publish(caption: str, lookback_min: int = 10) -> str | None:
    """Meta API 4xx 응답 직후 IG /media 목록을 조회해 실제 게시 여부를 확인.

    Meta 는 publish 처리가 끝났는데도 rate limit 계산 결과로 4xx (특히
    code=4, subcode=2207051) 를 응답하는 경우가 있다. 그대로 두면
    워크플로가 false-negative 로 실패 처리되어 cron/workflow_run 재시도가
    같은 콘텐츠를 다시 publish → 중복 게시 → action block 악화.

    caption 첫 줄(hook)이 lookback_min 분 이내 IG 게시물에 존재하면
    "실제로는 발행됨" 으로 보고 media_id 반환.
    """
    token = os.environ.get("META_ACCESS_TOKEN")
    ig_id = os.environ.get("IG_USER_ID")
    if not token or not ig_id:
        return None
    hook = next((ln.strip() for ln in caption.splitlines() if ln.strip()), "")[:40]
    if not hook:
        return None
    try:
        r = requests.get(
            f"{GRAPH}/{ig_id}/media",
            params={"fields": "id,timestamp,caption", "limit": 5, "access_token": token},
            timeout=15,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=lookback_min)
    for item in r.json().get("data", []):
        ts = item.get("timestamp", "")
        try:
            posted = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if posted < cutoff:
            continue
        if hook in (item.get("caption") or ""):
            return item.get("id")
    return None


def approved_dir(base: Path, day: dt.date, slot: str) -> Path:
    return base / day.isoformat() / slot / "approved"


def published_flag(base: Path, day: dt.date, slot: str) -> Path:
    # 발행 성공 시 기록하는 idempotency 마커. 같은 슬롯의 재실행(수동 re-run,
    # cron 지연 catch-up, 동시 트리거)에서 Meta 로 중복 업로드되는 걸 막는다.
    return base / day.isoformat() / slot / "published.flag"


def read_caption(captions_dir: Path, day: dt.date, slot: str) -> str:
    p = captions_dir / f"{day.isoformat()}_{slot}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def upload_to_cloudinary(images: list[Path], day: dt.date, slot: str) -> list[str]:
    # Cloudinary에 업로드 → secure_url 반환. CLOUDINARY_URL 환경변수로 자동 설정됨.
    # public_id 를 cardnews/<date>/<slot>/<stem> 으로 고정 + overwrite=True 라
    # 같은 슬롯 재발행 시 같은 URL 로 덮어써서 캐시/링크 안정.
    if not os.getenv("CLOUDINARY_URL"):
        raise RuntimeError("CLOUDINARY_URL 환경변수가 비어 있음")
    cloudinary.config(secure=True)
    urls: list[str] = []
    for img in images:
        res = cloudinary.uploader.upload(
            str(img),
            public_id=f"cardnews/{day.isoformat()}/{slot}/{img.stem}",
            overwrite=True,
            invalidate=True,
            resource_type="image",
        )
        urls.append(res["secure_url"])
    return urls


def _post(path: str, data: dict, max_attempts: int = 3) -> dict:
    """Meta Graph API POST with exponential backoff (2s/4s/8s).

    재시도 대상: 네트워크 예외, 5xx, 429 (rate limit).
    재시도 안 함: 4xx (요청 자체가 잘못된 경우 - 토큰 만료/권한 부족 등).
    """
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.post(f"{GRAPH}{path}", data=data, timeout=60)
        except requests.RequestException as e:
            last_err = e
        else:
            if r.ok:
                return r.json()
            status = r.status_code
            err = RuntimeError(f"Meta API {status}: {r.text}")
            if status < 500 and status != 429:
                # 토큰 만료/잘못된 요청은 재시도해도 같은 결과 → 즉시 raise
                raise err
            last_err = err
        if attempt < max_attempts:
            time.sleep(2 ** attempt)  # 2s, 4s, 8s
    assert last_err is not None
    raise last_err


def _wait_until_finished(creation_id: str, token: str, timeout_s: int = 120) -> None:
    """캐러셀 컨테이너 status_code = FINISHED 대기."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{creation_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )
        r.raise_for_status()
        status = r.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"container {creation_id} failed")
        time.sleep(3)
    raise TimeoutError(f"container {creation_id} not finished in {timeout_s}s")


def upload_carousel(image_urls: list[str], caption: str) -> str:
    token = os.environ["META_ACCESS_TOKEN"]
    ig_id = os.environ["IG_USER_ID"]

    pre = requests.get(
        f"{GRAPH}/{ig_id}",
        params={"fields": "id,username", "access_token": token},
        timeout=15,
    )
    if pre.status_code != 200:
        body = pre.text[:400]
        if '"code":190' in body:
            hint = "META_ACCESS_TOKEN 만료/무효 — 토큰 재발급 필요"
        elif '"error_subcode":33' in body or '"code":100' in body:
            hint = "토큰이 IG_USER_ID 읽기 불가 — IG_USER_ID 값 오류 또는 instagram_basic 스코프 누락"
        else:
            hint = "Graph API preflight (READ) 실패 — 토큰/ID/네트워크 점검"
        raise SystemExit(f"[preflight-read] {hint}: HTTP {pre.status_code} {body}")

    perm = requests.get(
        f"{GRAPH}/me/permissions",
        params={"access_token": token},
        timeout=15,
    )
    if perm.status_code == 200:
        granted = {p["permission"] for p in perm.json().get("data", []) if p.get("status") == "granted"}
        required = {"instagram_basic", "instagram_content_publish", "pages_show_list"}
        missing = required - granted
        if missing:
            raise SystemExit(
                f"[preflight-scope] 발행에 필요한 스코프 누락: {sorted(missing)} — "
                f"Graph API Explorer에서 해당 권한 체크 후 토큰 재발급. "
                f"granted={sorted(granted)}"
            )
    else:
        print(f"[preflight-scope] /me/permissions 조회 실패 (HTTP {perm.status_code}) — 스코프 확인 생략, publish 시도 진행")

    children: list[str] = []
    for url in image_urls:
        res = _post(f"/{ig_id}/media", {
            "image_url": url,
            "is_carousel_item": "true",
            "access_token": token,
        })
        children.append(res["id"])

    parent = _post(f"/{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": caption,
        "access_token": token,
    })
    _wait_until_finished(parent["id"], token)

    pub = _post(f"/{ig_id}/media_publish", {
        "creation_id": parent["id"],
        "access_token": token,
    })
    return pub["id"]


def notify(msg: str) -> None:
    url = os.getenv("NOTIFY_WEBHOOK_URL")
    if not url:
        print(msg)
        return
    try:
        requests.post(url, json={"text": msg}, timeout=10)
    except Exception as e:
        print(f"[notify-fail] {e}: {msg}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=["AM", "NOON", "PM"])
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    out_base = ROOT / cfg["paths"]["output"]
    folder = approved_dir(out_base, day, args.slot)
    if not folder.exists():
        notify(f"[skip] {day} {args.slot}: approved 폴더 없음")
        return 0

    flag = published_flag(out_base, day, args.slot)
    if flag.exists() and not args.dry_run:
        # 이미 발행된 슬롯. cron 지연 catch-up 이나 수동 re-run 으로 같은 슬롯이
        # 두 번 호출되면 Meta 가 중복 콘텐츠로 차단(code=4 "Application request
        # limit reached") → 멀쩡한 다음 슬롯까지 rate limit 에 걸린다.
        notify(f"[skip] {day} {args.slot}: 이미 발행됨 ({flag.read_text(encoding='utf-8').strip()})")
        return 0

    images = sorted(folder.glob("*.png"))
    if not (3 <= len(images) <= 10):
        notify(f"[skip] {day} {args.slot}: 캐러셀 매수 비정상 ({len(images)})")
        return 0

    caption = read_caption(ROOT / cfg["paths"]["captions"], day, args.slot)
    urls = upload_to_cloudinary(images, day, args.slot)

    if args.dry_run:
        print("[dry-run] 업로드 대상")
        for u in urls:
            print(" -", u)
        print("caption:", caption[:120])
        return 0

    try:
        media_id = upload_carousel(urls, caption)
    except Exception as e:
        verified_id = _verify_recent_publish(caption)
        if verified_id:
            media_id = verified_id
            notify(
                f"[ok-recovered] {day} {args.slot}: API 에러 무시 — IG에 게시 확인됨 "
                f"(media_id={media_id}). 원에러: {e}"
            )
        else:
            notify(f"[fail] {day} {args.slot}: {e}")
            return 1

    flag.write_text(f"media_id={media_id}\npublished_at={dt.datetime.utcnow().isoformat()}Z\n",
                    encoding="utf-8")
    notify(f"[ok] {day} {args.slot} 업로드 완료 (media_id={media_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
