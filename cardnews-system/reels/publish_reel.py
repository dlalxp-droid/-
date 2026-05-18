"""
릴스 인스타그램 발행 (Phase 3).

1. (옵션) BGM 합성 — bgm/ 폴더에 mp3 5개 있으면 요일별 회전, 없으면 무음
2. Cloudinary video 업로드 → secure_url
3. IG Graph API: POST /{ig_id}/media (media_type=REELS, video_url) → container_id
4. status_code 가 FINISHED 될 때까지 폴링 (REELS 는 image 보다 오래 걸림)
5. POST /{ig_id}/media_publish (creation_id) → media_id
6. published.flag 작성

사용:
  python publish_reel.py --date 2026-05-18 --slot AM
  python publish_reel.py --date 2026-05-18 --slot AM --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CARDNEWS_ROOT = ROOT.parent
GRAPH = "https://graph.facebook.com/v21.0"


# ----------------------------- BGM ----------------------------------------

def _bgm_track_for(day: dt.date) -> Path | None:
    """요일별 BGM 회전 (월~금 = 1~5, 주말은 1·2 재활용)."""
    bgm_dir = ROOT / "bgm"
    if not bgm_dir.exists():
        return None
    idx = day.weekday()  # 월=0
    if idx >= 5:
        idx -= 5  # 토→1, 일→2
    track = bgm_dir / f"track_{idx + 1}.mp3"
    return track if track.exists() else None


def mux_bgm(mp4_in: Path, bgm: Path | None, mp4_out: Path) -> None:
    """BGM 합성. BGM 이 없어도 IG Reels API 가 무음 영상을 reject 하므로
    무음 AAC 트랙이라도 반드시 채워준다."""
    if bgm is None:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(mp4_in),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            "-movflags", "+faststart",
            str(mp4_out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(mp4_in),
            "-stream_loop", "-1", "-i", str(bgm),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-af", "afade=t=out:st=14:d=1,volume=0.6",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            "-movflags", "+faststart",
            str(mp4_out),
        ]
    subprocess.run(cmd, check=True)


# ----------------------------- Cloudinary --------------------------------

def upload_video(mp4: Path, day: dt.date, slot: str) -> str:
    """Cloudinary 에 비디오 업로드 → IG fetch 가능한 직배 MP4 URL 반환.

    Meta error 2207077 ("Media upload has failed") 회피 위해:
    1. eager transformation 으로 IG 호환 MP4 (h264/aac) 명시적 생성 + sync 대기
    2. HEAD 요청으로 URL 실제 접근성 검증
    3. CDN 전파 위해 추가 대기
    """
    import cloudinary
    import cloudinary.uploader
    if not os.getenv("CLOUDINARY_URL"):
        raise RuntimeError("CLOUDINARY_URL 환경변수가 비어 있음")
    cloudinary.config(secure=True)
    res = cloudinary.uploader.upload(
        str(mp4),
        public_id=f"reels/{day.isoformat()}/{slot}",
        overwrite=True,
        invalidate=True,
        resource_type="video",
        format="mp4",
        eager=[{
            "format": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
        }],
        eager_async=False,
    )
    # eager URL 있으면 우선 사용 (IG 호환 보장된 derivative)
    if res.get("eager"):
        url = res["eager"][0]["secure_url"]
    else:
        url = res["secure_url"]

    # CDN 전파 대기 (Meta 가 fetch 시도하기 전 모든 엣지에 도달하도록)
    time.sleep(10)

    # HEAD 검증: Meta 가 받게 될 응답이 정상인지 미리 확인
    try:
        h = requests.head(url, timeout=20, allow_redirects=True)
        ct = h.headers.get("Content-Type", "")
        cl = h.headers.get("Content-Length", "?")
        print(f"  HEAD {url}: {h.status_code}, Content-Type={ct}, Content-Length={cl}")
        if h.status_code != 200:
            raise RuntimeError(f"Cloudinary URL not 200 (got {h.status_code}) — Meta 도 fetch 실패할 것")
    except requests.RequestException as e:
        print(f"  [warn] HEAD 실패: {e} — 그래도 IG 시도")

    return url


# ----------------------------- Graph API ---------------------------------

def _post(path: str, data: dict, max_attempts: int = 3) -> dict:
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.post(f"{GRAPH}{path}", data=data, timeout=120)
        except requests.RequestException as e:
            last_err = e
        else:
            if r.ok:
                return r.json()
            err = RuntimeError(f"Meta API {r.status_code}: {r.text}")
            if r.status_code < 500 and r.status_code != 429:
                raise err
            last_err = err
        if attempt < max_attempts:
            time.sleep(2 ** attempt)
    raise last_err


def _preflight(token: str, ig_id: str) -> None:
    """카드뉴스 scheduler 와 동일한 진단 패턴."""
    r = requests.get(f"{GRAPH}/{ig_id}",
                     params={"fields": "id,username", "access_token": token}, timeout=15)
    if r.status_code != 200:
        raise SystemExit(f"[preflight-read] IG_USER_ID/토큰 점검 필요: HTTP {r.status_code} {r.text[:300]}")
    rp = requests.get(f"{GRAPH}/me/permissions",
                      params={"access_token": token}, timeout=15)
    if rp.status_code == 200:
        granted = {p["permission"] for p in rp.json().get("data", []) if p.get("status") == "granted"}
        required = {"instagram_basic", "instagram_content_publish", "pages_show_list"}
        missing = required - granted
        if missing:
            raise SystemExit(f"[preflight-scope] 스코프 누락: {sorted(missing)} granted={sorted(granted)}")


def _wait_finished(container_id: str, token: str, timeout_s: int = 300) -> None:
    """REELS 컨테이너는 이미지보다 오래 걸린다(영상 인코딩). 최대 5분 대기.
    ERROR 시 Meta 의 status 상세 메시지까지 raise 에 포함."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        status_code = body.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise RuntimeError(
                f"container {container_id} ERROR — status='{body.get('status')}'"
            )
        time.sleep(5)
    raise TimeoutError(f"container {container_id} not finished in {timeout_s}s")


def publish_reel(video_url: str, caption: str) -> str:
    token = os.environ["META_ACCESS_TOKEN"]
    ig_id = os.environ["IG_USER_ID"]
    _preflight(token, ig_id)

    container = _post(f"/{ig_id}/media", {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token,
    })
    _wait_finished(container["id"], token)

    pub = _post(f"/{ig_id}/media_publish", {
        "creation_id": container["id"],
        "access_token": token,
    })
    media_id = pub["id"]

    # 사후 검증: media_publish 가 200 OK 응답해도 IG 가 백그라운드에서
    # 게시물을 보류/리젝트 하는 경우가 있다. 직접 GET 해서 permalink 가
    # 채워져 있는지 확인 → workflow 로그에 permalink 찍어서 즉시 검증 가능.
    time.sleep(3)
    try:
        v = requests.get(
            f"{GRAPH}/{media_id}",
            params={
                "fields": "id,media_type,media_product_type,permalink,timestamp",
                "access_token": token,
            },
            timeout=20,
        )
        if v.status_code == 200:
            j = v.json()
            print(f"  verified: media_product_type={j.get('media_product_type')} "
                  f"permalink={j.get('permalink')} timestamp={j.get('timestamp')}")
        else:
            print(f"  [warn] 발행 후 media_id 조회 실패 (HTTP {v.status_code}): {v.text[:200]}")
    except requests.RequestException as e:
        print(f"  [warn] 발행 후 검증 호출 실패: {e}")

    return media_id


# ----------------------------- 캡션 ---------------------------------------

def _build_reel_caption(date: str, slot: str) -> str:
    """카드뉴스 caption 을 재활용. 동일 톤 유지."""
    cap_path = CARDNEWS_ROOT / "captions" / f"{date}_{slot}.txt"
    if cap_path.exists():
        return cap_path.read_text(encoding="utf-8").strip()
    return ""


# ----------------------------- 진입점 -------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--slot", default="AM", choices=["AM", "NOON", "PM"])
    parser.add_argument("--mp4", default=None,
                        help="입력 MP4 경로. 미지정 시 output/{date}/REEL/reel_{slot}.mp4")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    date_str = args.date
    if len(date_str) == 8 and date_str.isdigit():
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    args.date = date_str
    day = dt.date.fromisoformat(date_str)
    mp4_in = Path(args.mp4) if args.mp4 else (
        CARDNEWS_ROOT / "output" / args.date / "REEL" / f"reel_{args.slot}.mp4"
    )
    if not mp4_in.exists():
        print(f"MP4 not found: {mp4_in} — make_reel.py 먼저 실행", file=sys.stderr)
        return 2

    out_dir = mp4_in.parent
    flag = out_dir / f"published_{args.slot}.flag"
    if flag.exists() and not args.dry_run:
        print(f"[skip] {args.date} {args.slot}: 이미 발행됨")
        return 0

    # BGM 합성
    bgm = _bgm_track_for(day)
    final_mp4 = out_dir / f"reel_{args.slot}_final.mp4"
    print(f"BGM: {bgm.name if bgm else 'silent'}")
    mux_bgm(mp4_in, bgm, final_mp4)

    caption = _build_reel_caption(args.date, args.slot)
    if not caption:
        print(f"[warn] caption 비어 있음 - {args.date}_{args.slot}.txt 확인", file=sys.stderr)

    if args.dry_run:
        print(f"[dry-run] would upload {final_mp4} → IG with caption ({len(caption)} chars)")
        return 0

    print(f"uploading {final_mp4} to Cloudinary")
    video_url = upload_video(final_mp4, day, args.slot)
    print(f"video_url: {video_url}")

    print("publishing to IG (REELS)")
    media_id = publish_reel(video_url, caption)
    print(f"[ok] media_id={media_id}")

    flag.write_text(
        f"media_id={media_id}\npublished_at={dt.datetime.utcnow().isoformat()}Z\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
