"""
카드뉴스 caption → 릴스 ReelSpec 자동 생성 (LLM).

cardnews-system/captions/{date}_{slot}.txt 의 caption 을 읽고
Claude 에 같은 주제·톤으로 릴스 텍스트 JSON 변환을 요청한다.

같은 캡션에서 매번 같은 결과가 나오게 하려면 deterministic
sampling 필요하지만, 약간의 변주가 있는 게 자연스러우니 그대로 둠.

API 키: ANTHROPIC_API_KEY 환경변수.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from render_frame import ReelSpec

ROOT = Path(__file__).resolve().parent
CARDNEWS_ROOT = ROOT.parent


SYSTEM_PROMPT = """너는 보험설계사용 인스타그램 릴스 콘텐츠 작가다.

입력: 같은 주제로 발행될 카드뉴스의 인스타 캡션 본문.
출력: 그 캡션과 같은 페르소나·톤을 이어받아 손글씨 노트풍 15초 릴스에
들어갈 텍스트 JSON.

## 릴스 레이아웃 (1080×1920, 손글씨 노트)

- 우상단 노란 포스트잇: 카테고리 라벨 (4~8자, 예: "부부 상담", "어린이보험",
  "DB 콜", "거절 처리", "재무 설계")
- 대제목 2줄: 페인포인트 질문형 (각 줄 8~13자, <em> 강조 없음)
- 본문 2줄: 핵심 페인포인트 (각 줄 13~17자). 첫 줄에 노란 형광펜 자동
- 인용 2줄: 회색 펜으로 적은 고객의 흔한 멘트 (각 줄 13~20자, " " 따옴표 포함)
- 화살표 ↓ + "근데..." 전환 (자동, 변경 안 함)
- 빨간 펜 핵심 2줄: 결론 메시지 (각 줄 8~14자). 빨간 물결 밑줄 자동
- 체크리스트 5개: 실전 팁 (각 항목 12~18자). 마지막 2개에 형광펜 자동 강조
- 별 옆 보너스 한 줄: 면책 디스클레이머
- 하단 포스트잇 CTA: "댓글 '정보' 남기면 DM 드려요" (고정)

## 광고규제 (반드시 회피)

금지:
- 단정형 ("100%", "무조건", "확실히 환급", "최고", "유일")
- 갈아타기 권유 ("기존 X 해지하고 Y로")
- 환급률·수익 과장 ("1억 내고 3억 받는", 환급률을 보장처럼)
- 객관 근거 없는 비교 ("A사보다 싸다", "타사 대비 2배")
- 보험사명/상품명 직접 ("○○보험엔", "○○특약" 으로 일반화)

안전:
- "달라질 수 있어요", "조건에 따라 가능"
- 보장 카테고리·체크리스트·정보 제공 톤
- 일당×일수 같은 계산은 독자가 직접 하게 (단정적 인상 방지)

## 길이 제한 (반드시 지킬 것 — 캔버스 오버플로우 방지)

headline: 각 줄 13자 이내
body: 각 줄 17자 이내
quote: 각 줄 20자 이내
red_lines: 각 줄 14자 이내
checklist: 각 항목 18자 이내
category: 8자 이내
bonus: 24자 이내 (보통 "*보장은 가입 조건·약관 따라 달라요" 같은 디스클레이머)

## 출력 JSON 스키마 (이것만 출력, 다른 텍스트 일체 금지)

{
  "category": "...",
  "headline": ["...", "..."],
  "body":     ["...", "..."],
  "quote":    ["...", "..."],
  "red_lines":["...", "..."],
  "checklist":["...", "...", "...", "...", "..."],
  "bonus":    "*...",
  "cta":      "댓글 '정보' 남기면 DM 드려요"
}
"""


# 길이 제한 (캔버스 오버플로 방지용 백업)
LIMITS = {
    "category": 8,
    "headline": 13,
    "body": 17,
    "quote": 20,
    "red_lines": 14,
    "checklist": 18,
    "bonus": 28,
}


def _truncate(s: str, limit: int) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _validate(data: dict) -> dict:
    """LLM 응답이 길이 제한 초과하면 잘라낸다."""
    data["category"] = _truncate(data.get("category", "보험 상담"), LIMITS["category"])
    for key in ("headline", "body", "quote", "red_lines"):
        items = data.get(key, [])[:2]
        while len(items) < 2:
            items.append("")
        data[key] = [_truncate(s, LIMITS[key]) for s in items]
    items = data.get("checklist", [])[:5]
    while len(items) < 5:
        items.append("...")
    data["checklist"] = [_truncate(s, LIMITS["checklist"]) for s in items]
    data["bonus"] = _truncate(data.get("bonus", "*보장은 가입 조건·약관 따라 달라요"),
                              LIMITS["bonus"])
    data["cta"] = data.get("cta", "댓글 ‘정보’ 남기면 DM 드려요")
    return data


def generate_spec_from_caption(caption: str) -> ReelSpec:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic SDK 필요: pip install anthropic") from e

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수 비어 있음")

    client = anthropic.Anthropic()
    model = os.getenv("LLM_MODEL", "claude-opus-4-7")
    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"다음 카드뉴스 캡션과 같은 주제·톤으로 릴스 JSON 생성:\n\n---\n{caption}\n---",
        }],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"LLM 응답에서 JSON 못 찾음:\n{text[:500]}")
    data = _validate(json.loads(m.group(0)))

    return ReelSpec(
        category=data["category"],
        headline=data["headline"],
        body=data["body"],
        quote=data["quote"],
        red_lines=data["red_lines"],
        checklist=data["checklist"],
        highlight_checks=2,
        bonus=data["bonus"],
        cta=data["cta"],
    )


def load_caption(date: str, slot: str) -> str:
    cap = CARDNEWS_ROOT / "captions" / f"{date}_{slot}.txt"
    if not cap.exists():
        raise FileNotFoundError(f"caption not found: {cap}")
    return cap.read_text(encoding="utf-8").strip()


def get_spec(date: str, slot: str) -> ReelSpec:
    return generate_spec_from_caption(load_caption(date, slot))
