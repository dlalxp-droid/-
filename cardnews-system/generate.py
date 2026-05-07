"""
카드뉴스 자동 생성기.

- LLM(Claude)로 보험 상담 화법 콘텐츠를 생성
- HTML 템플릿에 주입 후 Playwright로 1080x1080 PNG 캡처
- /output/YYYY-MM-DD/{AM|PM}/draft/  에 저장 (검수 후 approved/ 로 이동해야 업로드 큐 진입)

최소 동작 버전 사용 예:
    python generate.py --topic "거절 처리 화법" --preview
    python generate.py --days 7 --topic "보험 상담 화법" --slots AM,PM
    python generate.py --topic "거절 처리 화법" --preview --no-llm   # LLM 없이 더미 콘텐츠로 1세트 렌더
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# ----------------------------- 콘텐츠 모델 ---------------------------------

@dataclass
class CardSpec:
    """카드 1장의 렌더링 입력."""
    page: int
    total: int
    eyebrow: str = ""
    title: str = ""
    subtitle: str = ""
    body_html: str = ""
    swipe: str = ""
    cover: bool = False


@dataclass
class CardSet:
    """카드뉴스 1세트(한 게시물) = 8~10장."""
    topic: str
    slug: str
    cards: list[CardSpec] = field(default_factory=list)
    caption: str = ""


# ----------------------------- LLM 호출 -----------------------------------

SYSTEM_PROMPT = """너는 한국 보험설계사를 위한 카드뉴스 콘텐츠 작가다.
주제는 '보험 상담 화법'에 한정한다. 보험금 청구·심사·약관 해석·보상 분쟁은 절대 다루지 말 것.

각 세트는 8~10장 구조로, 다음을 반드시 포함한다:
1) 표지 (강한 한 줄 후킹)
2) 문제 상황 (현장에서 자주 겪는 장면)
3) 흔한 실수 멘트 (Before)
4) 권장 화법 (After) - Before/After 대비
5) 심리학 근거 1개 (카네기/아들러/치알디니/매슬로 등 중 1개)
6) 실전 적용 팁 2~3개
7) 한 줄 요약
8) CTA (저장/공유 유도, DM 유도 금지)

출력은 반드시 JSON. 스키마:
{
  "topic": "...",
  "caption": "인스타 캡션. 해시태그 5~10개 포함.",
  "cards": [
    {
      "kind": "cover|problem|before|after|insight|tips|summary|cta",
      "eyebrow": "상단 라벨(짧게)",
      "title": "큰 제목. 핵심 단어는 <em>강조</em> 가능",
      "subtitle": "보조 카피 (없으면 빈 문자열)",
      "bullets": ["불릿1","불릿2"],            // 선택
      "before": "Before 멘트",                 // kind=before/after 일 때
      "after": "After 멘트",                   // kind=before/after 일 때
      "ref_tag": "AUTHORITY",                  // kind=insight 일 때
      "ref_body": "심리학 근거 본문",           // kind=insight 일 때
      "swipe": "다음 페이지 유도 문구"
    }
  ]
}
"""


def call_llm(topic: str) -> dict:
    """Claude에 카드뉴스 1세트 콘텐츠를 요청."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK가 필요합니다. pip install anthropic")

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.getenv("LLM_MODEL", "claude-opus-4-7"),
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"주제: {topic}\n8~10장의 카드뉴스 1세트를 위 JSON 스키마로 생성하라.",
        }],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    # JSON만 추출
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"LLM 응답에서 JSON을 찾지 못함:\n{text[:500]}")
    return json.loads(m.group(0))


def dummy_payload(topic: str) -> dict:
    """LLM 없이 동작 확인용 더미 콘텐츠."""
    return {
        "topic": topic,
        "caption": (
            f"[{topic}]\n"
            "거절은 거부가 아니라 '아직 이해하지 못함'입니다.\n"
            "현장에서 그대로 쓰는 멘트 8장 정리.\n\n"
            "#보험설계사 #보험상담 #영업화법 #FC #GA #재무설계 #생명보험 #보험영업"
        ),
        "cards": [
            {"kind": "cover",
             "eyebrow": "TALK SCRIPT 01",
             "title": "“생각해볼게요”에<em>지지 않는 법</em>",
             "subtitle": "거절은 거부가 아니라, 아직 이해하지 못한 신호입니다.",
             "swipe": "SWIPE"},
            {"kind": "problem",
             "eyebrow": "PROBLEM",
             "title": "왜<em>매번 같은 자리</em>에서 막힐까",
             "subtitle": "마지막 5분에 ‘생각해볼게요’ 한마디로 상담이 끝난다.",
             "bullets": [
                 "고객은 결정을 미루는 게 아니라, 결정할 근거가 부족한 것",
                 "‘생각해볼게요’ = ‘납득되는 한 마디가 더 필요해요’",
             ],
             "swipe": "흔한 실수"},
            {"kind": "before",
             "eyebrow": "BEFORE",
             "title": "이렇게 말하면<em>대화가 끊깁니다</em>",
             "before": "“그럼 편하게 생각해보시고 연락 주세요.”",
             "after":  "그 자리에서 다음 약속이 잡히지 않으면, 80%는 다시 안 옵니다.",
             "swipe": "권장 화법"},
            {"kind": "after",
             "eyebrow": "AFTER",
             "title": "<em>한 번 더</em> 듣게 만드는 멘트",
             "before": "(고객) “생각해볼게요.”",
             "after":  "“네, 충분히요. 다만 어떤 부분이 가장 망설여지시는지만 한 가지 듣고 갈게요.”",
             "swipe": "왜 통할까"},
            {"kind": "insight",
             "eyebrow": "INSIGHT",
             "title": "거절 뒤에는<em>구체적 불안</em>이 숨어있다",
             "ref_tag": "DALE CARNEGIE",
             "ref_body": "“상대를 설득하려 하지 말고, 상대가 스스로 말하게 하라.” — 사람은 자기가 말한 이유에만 책임을 진다.",
             "swipe": "현장 적용"},
            {"kind": "tips",
             "eyebrow": "FIELD TIPS",
             "title": "오늘<em>바로</em> 써먹는 3가지",
             "bullets": [
                 "‘무엇이’ 망설여지는지 단어 하나만 끌어낸다",
                 "그 단어를 그대로 반복해 인정한다 (라벨링)",
                 "다음 만남 날짜를 ‘질문’이 아니라 ‘제안’으로 닫는다",
             ],
             "swipe": "요약"},
            {"kind": "summary",
             "eyebrow": "SUMMARY",
             "title": "거절은<em>닫힘이 아니라 신호</em>다",
             "subtitle": "한 단어만 더 듣고 가라. 그 단어가 다음 약속을 만든다.",
             "swipe": "저장 / 공유"},
            {"kind": "cta",
             "eyebrow": "SAVE",
             "title": "다음 상담 전,<em>이 카드를</em> 다시 펴세요",
             "subtitle": "내일 미팅 1건이 바뀝니다.",
             "swipe": ""},
        ],
    }


# ----------------------------- 카드 → HTML --------------------------------

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title_html(s: str) -> str:
    """타이틀은 <em> 태그만 허용 (LLM이 강조 표시할 때 사용)."""
    safe = _esc(s)
    safe = safe.replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")
    return safe


def _body_html(card: dict) -> str:
    kind = card.get("kind", "")
    if kind in ("before", "after"):
        before = _esc(card.get("before", ""))
        after  = _esc(card.get("after", ""))
        return f"""
        <div class="ba">
          <div class="row before"><div class="tag">BEFORE</div><div class="line">{before}</div></div>
          <div class="row after"><div class="tag">AFTER</div><div class="line">{after}</div></div>
        </div>"""
    if kind == "insight":
        return f"""
        <div class="ref">
          <div class="ref-tag">{_esc(card.get('ref_tag','REFERENCE'))}</div>
          <div class="ref-body">{_esc(card.get('ref_body',''))}</div>
        </div>"""
    bullets = card.get("bullets") or []
    if bullets:
        items = "\n".join(f"<li>{_esc(b)}</li>" for b in bullets)
        return f'<ul class="bullets">{items}</ul>'
    return ""


def render_card_html(template: str, spec: CardSpec, brand_name: str, brand_meta: str) -> str:
    """아주 단순한 mustache-lite 치환."""
    html = template
    repl = {
        "PAGE": str(spec.page),
        "TOTAL": str(spec.total),
        "EYEBROW": _esc(spec.eyebrow),
        "TITLE": _title_html(spec.title),
        "SUBTITLE": _esc(spec.subtitle),
        "BODY_HTML": spec.body_html,
        "SWIPE": _esc(spec.swipe),
        "BRAND": _esc(brand_name),
        "META": _esc(brand_meta),
        "COVER_CLASS": "cover" if spec.cover else "",
    }

    # 조건 블록 {{#KEY}} ... {{/KEY}}
    def cond(key: str, value: str, src: str) -> str:
        pattern = re.compile(r"\{\{#" + key + r"\}\}([\s\S]*?)\{\{/" + key + r"\}\}")
        if value:
            return pattern.sub(r"\1", src)
        return pattern.sub("", src)

    for k in ("EYEBROW", "TITLE", "SUBTITLE", "SWIPE"):
        html = cond(k, repl[k], html)

    # {{{TITLE}}} (raw) 처리 후 일반 {{KEY}} 치환
    html = html.replace("{{{TITLE}}}", repl["TITLE"])
    for k, v in repl.items():
        html = html.replace("{{" + k + "}}", v)
    return html


# ----------------------------- 페이로드 → CardSet -------------------------

def payload_to_set(payload: dict) -> CardSet:
    cards_in = payload.get("cards", [])
    total = len(cards_in)
    if not (8 <= total <= 10):
        # 너무 모자라거나 넘치면 자르거나 패딩 (운영상 LLM이 가끔 어김)
        cards_in = cards_in[:10]
        total = len(cards_in)

    specs: list[CardSpec] = []
    for i, c in enumerate(cards_in, start=1):
        specs.append(CardSpec(
            page=i,
            total=total,
            eyebrow=c.get("eyebrow", ""),
            title=c.get("title", ""),
            subtitle=c.get("subtitle", ""),
            body_html=_body_html(c),
            swipe=c.get("swipe", ""),
            cover=(c.get("kind") == "cover"),
        ))

    topic = payload.get("topic", "보험 상담 화법")
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", topic).strip("-")[:40] or "set"
    return CardSet(topic=topic, slug=slug, cards=specs, caption=payload.get("caption", ""))


# ----------------------------- 렌더링 ------------------------------------

def render_set(card_set: CardSet, out_dir: Path, brand_name: str, brand_meta: str) -> list[Path]:
    from playwright.sync_api import sync_playwright

    template = (ROOT / "templates" / "card.html").read_text(encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1080, "height": 1080},
                                      device_scale_factor=2)
        page = context.new_page()
        for spec in card_set.cards:
            html = render_card_html(template, spec, brand_name, brand_meta)
            page.set_content(html, wait_until="networkidle")
            png_path = out_dir / f"{spec.page:02d}.png"
            page.screenshot(path=str(png_path), full_page=False, omit_background=False,
                            clip={"x": 0, "y": 0, "width": 1080, "height": 1080})
            paths.append(png_path)
        browser.close()
    return paths


# ----------------------------- CLI ---------------------------------------

def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def slot_dir(base: Path, day: dt.date, slot: str) -> Path:
    return base / day.isoformat() / slot / "draft"


def write_caption(captions_dir: Path, day: dt.date, slot: str, caption: str) -> Path:
    captions_dir.mkdir(parents=True, exist_ok=True)
    p = captions_dir / f"{day.isoformat()}_{slot}.txt"
    p.write_text(caption, encoding="utf-8")
    return p


def generate_one(topic: str, day: dt.date, slot: str, cfg: dict, use_llm: bool) -> CardSet:
    payload = call_llm(topic) if use_llm else dummy_payload(topic)
    card_set = payload_to_set(payload)

    out_base = ROOT / cfg["paths"]["output"]
    out = slot_dir(out_base, day, slot)
    render_set(card_set, out, cfg["brand"]["name"], cfg["brand"]["meta"])

    write_caption(ROOT / cfg["paths"]["captions"], day, slot, card_set.caption)
    print(f"[OK] {day} {slot}  {len(card_set.cards)}장 → {out}")
    return card_set


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="보험 상담 화법")
    parser.add_argument("--days", type=int, default=1, help="며칠치 생성 (기본 1)")
    parser.add_argument("--slots", default="AM", help="콤마구분: AM,PM")
    parser.add_argument("--start", default=None, help="시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--preview", action="store_true", help="오늘 AM 한 세트만 생성")
    parser.add_argument("--no-llm", action="store_true", help="더미 페이로드로 동작 확인")
    args = parser.parse_args()

    cfg = load_config()
    use_llm = not args.no_llm and bool(os.getenv("ANTHROPIC_API_KEY"))
    if not use_llm and not args.no_llm:
        print("[warn] ANTHROPIC_API_KEY 미설정 → 더미 콘텐츠로 진행", file=sys.stderr)

    today = dt.date.fromisoformat(args.start) if args.start else dt.date.today()

    if args.preview:
        generate_one(args.topic, today, "AM", cfg, use_llm=use_llm)
        return 0

    slots = [s.strip() for s in args.slots.split(",") if s.strip()]
    for d in range(args.days):
        day = today + dt.timedelta(days=d)
        for slot in slots:
            generate_one(args.topic, day, slot, cfg, use_llm=use_llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
