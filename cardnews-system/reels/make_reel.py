"""
릴스 MP4 생성기 (Phase 2).

cardnews-system/reels/make_reel.py --date 2026-05-18 --slot AM

- render_frame 으로 450 프레임 렌더
- ffmpeg 로 H.264 1080x1920 30fps MP4 인코딩 (무음)
- BGM 은 publish_reel.py 단계에서 합성 (Phase 3)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from render_frame import ReelSpec, render_frame, ROOT as REELS_ROOT

FPS = 30
DURATION = 15.0
TOTAL_FRAMES = int(FPS * DURATION)


# 손으로 매핑한 spec 이 있으면 우선 사용. 없으면 LLM 으로 자동 생성.
HARDCODED_SPECS: dict[tuple[str, str], ReelSpec] = {
    ("2026-05-18", "AM"): ReelSpec(
        category="부부 상담",
        headline=["남편 한마디에", "계약이 엎어진다"],
        body=["결정권자 못 읽으면", "30분이 5분 됩니다"],
        quote=["“좀 더 생각해볼게요”", "마지막에 남편이 한마디"],
        red_lines=["아내가 아니라", "남편을 봐야 한다"],
        checklist=[
            "인사·자리 배치는 남편 먼저",
            "시선 분배 5:5 의식적으로",
            "결정 멘트는 남편에게 직접",
            "마무리 동의는 남편 답 받기",
            "후속 연락처는 둘 다 받기",
        ],
        highlight_checks=2,
        bonus="*상담 방향은 부부 상황 따라 달라요",
        cta="댓글 ‘정보’ 남기면 DM 드려요",
    ),
}


def _resolve_spec(date: str, slot: str) -> ReelSpec:
    key = (date, slot)
    if key in HARDCODED_SPECS:
        print(f"using hardcoded spec for {key}")
        return HARDCODED_SPECS[key]
    print(f"generating spec via LLM for {date} {slot}")
    from caption_to_spec import get_spec
    return get_spec(date, slot)


def render_all_frames(spec: ReelSpec, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for i in range(TOTAL_FRAMES):
        t = i / FPS
        img = render_frame(spec, t_sec=t)
        img.save(out_dir / f"f_{i:04d}.jpg", "JPEG", quality=88)
        if (i + 1) % 75 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{TOTAL_FRAMES} ({elapsed:.1f}s, {(i+1)/elapsed:.1f} fps)")


def encode_mp4(frames_dir: Path, mp4_out: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f_%04d.jpg"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-t", str(DURATION),
        str(mp4_out),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--slot", default="AM", choices=["AM", "NOON", "PM"])
    parser.add_argument("--out", default=None)
    parser.add_argument("--keep-frames", action="store_true")
    args = parser.parse_args()

    date = args.date
    if len(date) == 8 and date.isdigit():
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    args.date = date
    try:
        spec = _resolve_spec(date, args.slot)
    except FileNotFoundError as e:
        print(f"caption not found — 카드뉴스가 먼저 생성돼야 함: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"spec resolve 실패: {e}", file=sys.stderr)
        return 2

    if args.out:
        mp4_out = Path(args.out)
    else:
        out_dir = REELS_ROOT.parent / "output" / args.date / "REEL"
        out_dir.mkdir(parents=True, exist_ok=True)
        mp4_out = out_dir / f"reel_{args.slot}.mp4"

    work_dir = Path(tempfile.mkdtemp(prefix="reel_frames_"))
    try:
        print(f"rendering {TOTAL_FRAMES} frames (mood={spec.mood})")
        render_all_frames(spec, work_dir)
        print(f"encoding {mp4_out}")
        mp4_out.parent.mkdir(parents=True, exist_ok=True)
        encode_mp4(work_dir, mp4_out)
        # publish 단계에서 BGM 매칭용 사이드카
        mood_file = mp4_out.parent / f"mood_{args.slot}.txt"
        mood_file.write_text(spec.mood, encoding="utf-8")
        size_kb = mp4_out.stat().st_size // 1024
        print(f"OK: {mp4_out} ({size_kb} KB), mood saved to {mood_file.name}")
    finally:
        if not args.keep_frames:
            shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
