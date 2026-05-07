"""특정 날짜/슬롯의 draft 발행을 차단.

사용:
  python reject.py --date 2026-05-08 --slot AM --reason "보상 화법 들어감"

효과:
- output/<date>/<slot>/rejected.flag 생성
- 이후 cron의 approve 단계가 스킵됨 → 인스타 업로드 큐에 안 들어감
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None)
    p.add_argument("--slot", required=True, choices=["AM", "PM"])
    p.add_argument("--reason", default="rejected")
    args = p.parse_args()

    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    base = ROOT / "output" / day.isoformat() / args.slot
    base.mkdir(parents=True, exist_ok=True)
    flag = base / "rejected.flag"
    flag.write_text(f"{dt.datetime.utcnow().isoformat()}Z\n{args.reason}\n", encoding="utf-8")
    print(f"[ok] rejected: {day} {args.slot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
