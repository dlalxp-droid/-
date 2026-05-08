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

매 세트는 주어진 서브토픽의 고유한 각도로 작성한다. 다른 세트와 표현·예시·심리학 근거가
겹치지 않도록 의식적으로 다른 후킹 문구·다른 사례·다른 권위자(카네기/아들러/치알디니/매슬로/카네만 등)
를 선택할 것.

## 스토리 흐름 (반드시 결과 → 문제 → 해법 순서)

독자가 마지막 장까지 스와이프하게 만드는 핵심은 "결과를 먼저 보여주고 어떻게 그렇게 됐는지를 늦게
공개"하는 것이다. 첫 장에서 문제 상황부터 풀어쓰면 흥미가 빠르게 식는다.

각 세트는 8~10장이며 다음 순서로 구성한다:

1) cover    — 표지. 결과 티저(강한 한 줄). 어떤 변화/성과가 있었는지 암시만 하고 방법은 숨김.
2) result   — 실제 일어난 결과 장면. 구체적 숫자/반응 포함. 아직 '왜'는 공개하지 말 것.
3) tease    — "근데 시작은 정반대였다" 류 전환. 다음 장에서 문제를 공개한다고 예고.
4) problem  — 그제야 현장 문제 상황 공개.
5) before   — 흔한 실수 멘트(Before 한 줄).
6) after    — 결과를 만든 권장 멘트(After 한 줄). Before와 대비.
7) insight  — 왜 통했는지 심리학 근거 1개 (권위자 인용).
8) tips     — 오늘 바로 써먹는 팁 2~3개.
9) summary  — 한 줄 요약.
10) cta     — 저장 + 댓글/공유 유도 (DM 유도 금지).

8~9장으로 압축할 때는 result+tease 또는 summary+cta 를 한 장으로 합친다.
어떤 경우에도 cover 다음에 problem 이 바로 오면 안 된다 — 반드시 result/tease 로 호기심을 유지한다.

각 카드의 swipe 문구는 다음 장으로 넘기는 cliffhanger 역할:
- result 뒤: "어떻게 가능했을까", "비결은…"
- tease  뒤: "그 시작은…", "처음엔 정반대였다"
- problem 뒤: "흔한 실수부터", "Before"
- before 뒤: "그럼 어떻게?", "After"
독자가 다음 장을 안 보면 손해라고 느끼게 짧고 강하게 쓴다.

## 캡션 (Instagram caption)

자연스러운 톤. 사무적·교과서적 문장 금지. 동료 설계사에게 말하듯 1인칭/공감체로 쓴다.
이모지는 0~2개까지만, 과용 금지.

구조 (각 블록 사이 빈 줄 1개):
- 1줄 hook: 결과 티저 (표지와 같은 메시지를 다른 단어로). 끝에 "👇" 또는 "스와이프" 같은 유도 문구.
- 2~3문장 본문: 현장 톤. "저도 처음엔…" 같은 1인칭 OK.
- 댓글/공유 유도 한 줄: 구체 질문 + 저장/공유 권유.
  예) "여러분은 이럴 때 어떤 멘트 쓰세요? 댓글로 공유 부탁해요 🙏 도움됐으면 동료 설계사에게도 보내주세요."
- 해시태그 5~10개 (마지막 줄에만 모아쓰기).

## 카드 표현 규칙

title 안에서 강조는 <em>...</em>, 줄바꿈은 <br> 만 허용 (그 외 HTML 태그 금지).
- title 은 한 줄당 12~14자 이내로 짧게 끊어 쓴다.
- <em> 안에는 짧은 강조구문(2~7자)만. 길어서 두 줄로 깨지면 underline 가독성이 무너진다.
- 비교 표지("A vs B")는 <br>로 끊어 명확히 (예: '<em>A</em><br><em>B</em>').

본문 길이 제한 (반드시 지킬 것):
- subtitle: 60자 이내, 한 문장
- BEFORE / AFTER 라인: 각각 60자 이내, 인용 한 문장. 길어지면 핵심만 발췌.
- bullets: 항목당 50자 이내, 항목 수 3개 이내
- ref_body: 70자 이내

## 출력 스키마 (JSON only)

{
  "topic": "...",
  "caption": "위 구조의 인스타 캡션",
  "cards": [
    {
      "kind": "cover|result|tease|problem|before|after|insight|tips|summary|cta",
      "eyebrow": "상단 라벨(짧게)",
      "title": "큰 제목. 핵심 단어는 <em>강조</em> 가능",
      "subtitle": "보조 카피 (없으면 빈 문자열)",
      "bullets": ["불릿1","불릿2"],            // 선택
      // kind=before 카드: "before" 만 채운다 (after 는 생략 또는 빈 문자열)
      // kind=after  카드: "after"  만 채운다 (before 는 생략 또는 빈 문자열)
      // 한 카드에 Before/After 를 같이 보여주고 싶을 때만 둘 다 채운다.
      "before": "Before 멘트 (60자 이내)",
      "after": "After 멘트 (60자 이내)",
      "ref_tag": "AUTHORITY",                  // kind=insight 일 때
      "ref_body": "심리학 근거 본문",           // kind=insight 일 때
      "swipe": "다음 페이지 유도 문구 (cliffhanger)"
    }
  ]
}
"""


def call_llm(topic: str, recent_topics: list[str] | None = None) -> dict:
    """Claude에 카드뉴스 1세트 콘텐츠를 요청."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK가 필요합니다. pip install anthropic")

    avoid = ""
    if recent_topics:
        avoid = "\n이미 이번 주에 다룬 주제(표현/예시 겹치지 마라): " + ", ".join(recent_topics)

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.getenv("LLM_MODEL", "claude-opus-4-7"),
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"서브토픽: {topic}{avoid}\n8~10장의 카드뉴스 1세트를 위 JSON 스키마로 생성하라.",
        }],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    # JSON만 추출
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"LLM 응답에서 JSON을 찾지 못함:\n{text[:500]}")
    return json.loads(m.group(0))


def dummy_payload(topic: str, idx: int = 0) -> dict:
    """LLM 없이 동작 확인용 더미 콘텐츠. topic 별로 표지가 달라집니다."""
    eyebrow = f"TALK SCRIPT {idx+1:02d}"
    cover_title = f"<em>{topic}</em>" if topic else "<em>보험 상담 화법</em>"
    return {
        "topic": topic,
        "caption": (
            "‘생각해볼게요’ 한마디로 끝나던 상담이 다음 약속으로 바뀐 한 멘트가 있어요 👇\n"
            "\n"
            "저도 처음엔 매번 같은 자리에서 막혔거든요. 근데 마지막 한 단어만 바꿨더니\n"
            "그 자리에서 80%가 다음 미팅을 잡고 갔습니다. 카드에서 풀어볼게요.\n"
            "\n"
            "여러분은 이럴 때 어떤 멘트 쓰세요? 댓글로 한 줄만 공유 부탁해요 🙏\n"
            "도움됐으면 동료 설계사에게도 보내주시고, 다음 상담 전에 다시 펼 수 있게 저장해두세요.\n"
            "\n"
            "#보험설계사 #보험상담 #영업화법 #클로징 #FC #GA #재무설계 #생명보험 #보험영업"
        ),
        "cards": [
            {"kind": "cover",
             "eyebrow": eyebrow,
             "title": cover_title,
             "subtitle": "한 단어만 바꿨더니 다음 약속이 잡혔습니다.",
             "swipe": "어떻게?"},
            {"kind": "result",
             "eyebrow": "RESULT",
             "title": "그날<em>80%가</em> 다음 약속을 잡았다",
             "subtitle": "같은 멘트로 한 달, 마무리 5분이 완전히 달라졌습니다.",
             "swipe": "비결은…"},
            {"kind": "tease",
             "eyebrow": "BUT",
             "title": "근데 시작은<em>정반대</em>였다",
             "subtitle": "‘생각해볼게요’ 한마디로 매번 상담이 끝나던 자리.",
             "swipe": "그 시작은…"},
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
             "swipe": "그럼 어떻게?"},
            {"kind": "after",
             "eyebrow": "AFTER",
             "title": "<em>한 번 더</em> 듣게 만드는 멘트",
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
             "swipe": "마지막 부탁"},
            {"kind": "cta",
             "eyebrow": "SAVE & SHARE",
             "title": "댓글로<em>당신의 멘트</em>도 들려주세요",
             "subtitle": "도움됐으면 저장 + 동료에게 공유. 다음 상담 전에 다시 펴보세요.",
             "swipe": ""},
        ],
    }


# ----------------------------- 카드 → HTML --------------------------------

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title_html(s: str) -> str:
    """타이틀은 <em> (강조) 와 <br> (줄바꿈) 만 허용."""
    safe = _esc(s)
    safe = safe.replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")
    safe = (
        safe.replace("&lt;br&gt;", "<br>")
            .replace("&lt;br/&gt;", "<br>")
            .replace("&lt;br /&gt;", "<br>")
    )
    return safe


def _body_html(card: dict) -> str:
    kind = card.get("kind", "")
    if kind in ("before", "after"):
        before_text = (card.get("before") or "").strip()
        after_text = (card.get("after") or "").strip()
        # kind 가 명시한 쪽이 비어있고 반대쪽만 있으면 그 한 박스만 노출.
        # 둘 다 채워져 있으면 양쪽, 둘 다 비면 빈 카드 방지로 박스 자체 생략.
        rows: list[str] = []
        if before_text:
            rows.append(
                '<div class="row before"><div class="tag">BEFORE</div>'
                f'<div class="line">{_esc(before_text)}</div></div>'
            )
        if after_text:
            rows.append(
                '<div class="row after"><div class="tag">AFTER</div>'
                f'<div class="line">{_esc(after_text)}</div></div>'
            )
        if not rows:
            return ""
        return '<div class="ba">' + "".join(rows) + "</div>"
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

    brand_name = (brand_name or "").strip() or "Insurance Talk Notes"
    brand_meta = (brand_meta or "").strip()

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


def generate_one(
    topic: str,
    day: dt.date,
    slot: str,
    cfg: dict,
    use_llm: bool,
    idx: int = 0,
    recent_topics: list[str] | None = None,
) -> CardSet:
    payload = call_llm(topic, recent_topics=recent_topics) if use_llm else dummy_payload(topic, idx=idx)
    card_set = payload_to_set(payload)
    if not card_set.topic:
        card_set.topic = topic

    out_base = ROOT / cfg["paths"]["output"]
    out = slot_dir(out_base, day, slot)
    render_set(card_set, out, cfg["brand"]["name"], cfg["brand"]["meta"])

    write_caption(ROOT / cfg["paths"]["captions"], day, slot, card_set.caption)
    print(f"[OK] {day} {slot}  {len(card_set.cards)}장 ({topic}) → {out}")
    return card_set


def _resolve_topics(args, cfg: dict) -> list[str]:
    if args.topic:
        return [args.topic]
    pool = cfg.get("content", {}).get("topics") or []
    if pool:
        return pool
    return [cfg.get("content", {}).get("default_topic", "보험 상담 화법")]


def _check_brand(cfg: dict) -> None:
    name = (cfg.get("brand", {}).get("name") or "").strip()
    if not name or "여기에" in name or "본인 명의" in name:
        print(
            f"[warn] config.yaml 의 brand.name 이 placeholder({name!r}) 입니다. "
            "본인 이름/브랜드명으로 교체하세요.",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default=None,
                        help="단일 토픽 강제. 미지정 시 config.content.topics 풀 회전")
    parser.add_argument("--days", type=int, default=1, help="며칠치 생성 (기본 1)")
    parser.add_argument("--slots", default="AM", help="콤마구분: AM,PM")
    parser.add_argument("--start", default=None, help="시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--preview", action="store_true", help="오늘 AM 한 세트만 생성")
    parser.add_argument("--no-llm", action="store_true", help="더미 페이로드로 동작 확인")
    args = parser.parse_args()

    cfg = load_config()
    _check_brand(cfg)
    use_llm = not args.no_llm and bool(os.getenv("ANTHROPIC_API_KEY"))
    if not use_llm and not args.no_llm:
        print("[warn] ANTHROPIC_API_KEY 미설정 → 더미 콘텐츠로 진행", file=sys.stderr)

    today = dt.date.fromisoformat(args.start) if args.start else dt.date.today()
    topics = _resolve_topics(args, cfg)

    if args.preview:
        generate_one(topics[0], today, "AM", cfg, use_llm=use_llm, idx=0)
        return 0

    slots = [s.strip() for s in args.slots.split(",") if s.strip()]
    used: list[str] = []
    idx = 0
    for d in range(args.days):
        day = today + dt.timedelta(days=d)
        for slot in slots:
            topic = topics[idx % len(topics)]
            generate_one(topic, day, slot, cfg, use_llm=use_llm, idx=idx,
                         recent_topics=used[-6:] or None)
            used.append(topic)
            idx += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
