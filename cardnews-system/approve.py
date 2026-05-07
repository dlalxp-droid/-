"""draft → approved 자동 승급 (B안).

흐름:
- output/<date>/<slot>/draft/*.png 가 존재하고
- output/<date>/<slot>/rejected.flag 가 없으며
- output/<date>/<slot>/approved/ 가 비어있을 때
  → draft 의 PNG를 approved/ 로 이동.

수동 호출도 가능. cron(GitHub Actions)이 게시 N시간 전에 호출하는 게 기본 사용처.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def approve(day: dt.date, slot: str, force: bool = False) -> int:
    base = ROOT / "output" / day.isoformat() / slot
    draft = base / "draft"
    approved = base / "approved"
    flag = base / "rejected.flag"

    if not draft.exists() or not any(draft.glob("*.png")):
        print(f"[skip] no draft: {day} {slot}")
        return 0
    if flag.exists() and not force:
        print(f"[skip] rejected.flag present: {day} {slot}")
        return 0
    if approved.exists() and any(approved.glob("*.png")):
        print(f"[skip] already approved: {day} {slot}")
        return 0

    approved.mkdir(parents=True, exist_ok=True)
    n = 0
    for png in sorted(draft.glob("*.png")):
        png.rename(approved / png.name)
        n += 1
    print(f"[ok] approved {n} cards: {day} {slot}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘 KST)")
    p.add_argument("--slot", required=True, choices=["AM", "PM"])
    p.add_argument("--force", action="store_true", help="rejected.flag 무시")
    args = p.parse_args()

    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    return approve(day, args.slot, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
