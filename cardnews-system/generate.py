"""
카드뉴스 자동 생성기.

- LLM(Claude)로 보험 상담 화법 콘텐츠를 생성
- HTML 템플릿에 주입 후 Playwright로 1080x1080 PNG 캡처
- /output/YYYY-MM-DD/{AM|NOON|PM}/draft/  에 저장 (검수 후 approved/ 로 이동해야 업로드 큐 진입)

최소 동작 버전 사용 예:
    python generate.py --topic "거절 처리 화법" --preview
    python generate.py --days 7 --topic "보험 상담 화법" --slots AM,NOON,PM
    python generate.py --topic "거절 처리 화법" --preview --no-llm   # LLM 없이 더미 콘텐츠로 1세트 렌더
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
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

PROMPT_PREAMBLE = """너는 한국 보험설계사를 위한 카드뉴스 콘텐츠 작가다.
주제는 '보험 상담 화법'에 한정한다. 보험금 청구·심사·약관 해석·보상 분쟁은 절대 다루지 말 것.

매 세트는 주어진 서브토픽의 고유한 각도로 작성한다. 다른 세트와 표현·예시·심리학 근거가
겹치지 않도록 의식적으로 다른 후킹 문구·다른 사례·다른 권위자(카네기/아들러/치알디니/매슬로/카네만 등)
를 선택할 것.
"""

PROMPT_ANTI_AI_RULES = """## 자연스러움 규칙 (AI가 쓴 것처럼 보이면 실패)

아래는 지금까지 반복돼서 "AI 티" 나는 패턴이다. 절대 반복하지 말 것:

- 캡션을 "저도 처음엔…" 으로 시작하는 습관 — 화자 프로필이 그런 톤이 아니면 쓰지 마라.
  질문형, 장면 묘사, 단정적 선언, 대화 인용 등 다른 방식으로 열어라.
- "~한마디로 계약이 살아났어요/N건이 살아났어요/계약이 돌아왔어요" 류 상투 후킹 문구 반복.
  매 세트마다 후킹 구조 자체를 다르게 시도해라 (숫자 없이 장면으로 시작 / 고객 대화 인용으로
  시작 / 역설적 문장으로 시작 등).
- 근거 없이 지나치게 정밀한 통계를 "연구에 따르면"처럼 인용하는 것
  (예: "67%가", "1.7배 높다는 행동경제학 연구", "2.3배 빨라진다는 연구"). 실제 유명한 이론/저자를
  인용할 땐 이름을 대고, 그게 아니면 "체감상", "현장에서 보면", "열에 여섯은" 같은 경험적·모호한
  표현으로 바꿔라. 가짜 정밀도(소수점 배수, 딱 떨어지는 %)는 티가 난다.
- 문장 끝마다 "👇" 를 붙이는 습관. 이모지는 화자 프로필에 지정된 개수만 지켜라.
- 모든 문장이 비슷한 길이·리듬으로 대칭적인 것. 짧은 문장과 긴 문장을 섞고, 어미도
  ("~했거든요", "~하더라고요", "~습니다", "~죠") 한 세트 안에서 두세 종류로 섞어라.
- 완벽하게 정리된 3단 구조(문제-원인-해법)만 반복하는 것. 가끔은 결론부터 던지고 이유를
  나중에 대거나, 여담을 살짝 섞는 등 사람이 실제로 말할 때의 흐름을 넣어라.
"""

PROMPT_CARD_RULES = """## 카드 표현 규칙

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
  "caption": "위 캡션 톤 지침에 맞는 인스타 캡션",
  "cards": [
    {
      "kind": "위 스토리 흐름의 카드 종류 중 하나 (정확히 지정된 값 사용)",
      "eyebrow": "상단 라벨(짧게)",
      "title": "큰 제목. 핵심 단어는 <em>강조</em> 가능",
      "subtitle": "보조 카피 (없으면 빈 문자열)",
      "bullets": ["불릿1","불릿2"],            // 선택
      "before": "Before 멘트 (60자 이내, 해당 kind 일 때)",
      "after":  "After 멘트 (60자 이내, 해당 kind 일 때)",
      "ref_tag": "AUTHORITY",                  // 권위자 인용 카드일 때
      "ref_body": "심리학 근거 본문",           // 권위자 인용 카드일 때
      "swipe":   "다음 페이지 유도 문구 (cliffhanger)"
    }
  ]
}
"""

PROMPT_DM_CTA_RULES = """## 낚시(호기심 갭) + "댓글 키워드 또는 DM" CTA 규칙

이 계정의 전환 목표는 단순 저장이 아니라 댓글·DM 유입이다. 아래를 지킨다:

1) 카드 전체에서 "일반 원칙/왜 이게 통하는지"는 확실히 알려주되, 그 사람 상황에
   그대로 적용하면 안 되는 부분(정확한 숫자·상품명·화법의 디테일 한 조각)은 마지막
   카드 전까지 의도적으로 남겨둔다. "이 원칙은 맞는데, 내 케이스엔 어떻게 적용하지?"
   라는 궁금증이 남게 만드는 것이 목표 — 정보를 아예 안 주는 게 아니라 "일반론과
   맞춤 적용 사이의 gap"을 남기는 것.
2) 마지막 카드(kind: cta)는 저장/공유 유도가 아니라 "댓글 키워드 또는 DM" 유도로
   통일한다. 이번 세트 토픽을 대표하는 짧은 키워드(2~6자, 예: 고객이 자주 하는 말이나
   화법의 핵심 단어)를 하나 정해 따옴표로 표시하고, "'키워드' 댓글 또는 DM 주세요"
   계열의 의미를 반드시 담되 그대로 매번 복붙하지 말고 화자 말투·토픽에 맞게 자연스럽게
   표현을 바꿔라 (예: "'비싸요' 댓글 남기거나 DM 주시면 알려드릴게요" / "내 케이스는
   어떻게 다른지 궁금하면 '거절' 댓글이나 DM 주세요"). 매 세트 키워드는 그 세트만의
   고유 단어로 — 항상 같은 단어를 재사용하지 마라.
   보험 상품 추천/가입 유도가 아니라 "이 화법/정보를 더 알고 싶으면"의 맥락을 유지한다
   (표시광고 규정 — 특정 상품 가입을 직접 권유하는 문구는 쓰지 않는다).
3) 캡션 마지막 CTA 줄도 같은 원칙 — 카드 cta 에서 쓴 것과 같은 키워드로 "'키워드'
   댓글 또는 DM" 을 안내하되 표현은 카드와 캡션에서 서로 다르게 풀어써라.
"""

_LEGACY_STORY_FLOW = """## 스토리 흐름 (결과 → 문제 → 해법 순)

각 세트는 다음 10장 순서로 구성한다 (kind 정확히 이대로):
1) cover    — 결과 티저
2) result   — 구체 수치/반응
3) tease    — "근데 시작은 정반대였다"
4) problem  — 현장 문제 공개
5) before   — 흔한 실수 멘트
6) after    — 권장 멘트
7) insight  — 권위자(카네기/아들러/치알디니/매슬로/카네만) 1명 인용
8) tips     — 실전 팁 2~3개
9) summary  — 한 줄 요약
10) cta     — "키워드" 댓글 또는 DM 유도 (자세한 내용/내 케이스 맞춤 적용, 저장·공유 아님)
"""

_LEGACY_CAPTION_SECTION = """## 캡션 (Instagram caption) — 1인칭 경험형

자연스러운 톤. 사무적·교과서적 문장 금지. 동료 설계사에게 말하듯 1인칭/공감체.
이모지는 0~2개까지만, 과용 금지.

구조 (각 블록 사이 빈 줄 1개):
- 1줄 hook: 결과 티저 + 끝에 "👇" 또는 "스와이프" 유도
- 2~3문장 1인칭 회상 ("저도 처음엔…")
- "키워드" 댓글 또는 DM 유도 한 줄 (궁금증 자극 질문 + 카드 cta 와 같은 키워드로
  "'키워드' 댓글 또는 DM으로" 류 CTA)
- 마지막 줄 해시태그 5~10개
"""


def _story_flow_section(structure: dict | None) -> str:
    """config.content.story_structures 의 한 항목 → 프롬프트 블록."""
    if not structure or not structure.get("cards"):
        return _LEGACY_STORY_FLOW
    cards = structure["cards"]
    arc = (structure.get("arc") or "").strip()
    label = structure.get("label") or structure.get("name") or "story"
    card_list = "\n".join(f"{i+1}) {kind}" for i, kind in enumerate(cards))
    return (
        f"## 스토리 흐름 ({label})\n\n"
        f"{arc}\n\n"
        f"이번 세트는 다음 순서로 {len(cards)}장을 구성한다 (kind 값은 정확히 이대로 쓸 것):\n\n"
        f"{card_list}\n\n"
        "마지막 장은 항상 cta. 각 카드의 swipe 문구는 다음 장으로 넘기는 cliffhanger —\n"
        "독자가 다음 장을 안 보면 손해라고 느끼게 짧고 강하게 쓴다.\n"
    )


def _caption_section(caption_style: dict | None) -> str:
    """config.content.caption_styles 의 한 항목 → 프롬프트 블록."""
    if not caption_style or not caption_style.get("template"):
        return _LEGACY_CAPTION_SECTION
    label = caption_style.get("label") or caption_style.get("name") or "caption"
    template = caption_style["template"].rstrip()
    return (
        f"## 캡션 (Instagram caption) — {label}\n\n"
        "화자 프로필의 말투를 캡션에도 그대로 살려라. 사무적·교과서적 문장 금지.\n\n"
        "구조 (각 블록 사이 빈 줄 1개):\n"
        f"{template}\n"
    )


def _persona_section(persona: dict | None) -> str:
    """config.content.writer_personas 의 한 항목 → 프롬프트 블록.

    매 세트마다 다른 '화자'를 지정해 문체를 강제로 다르게 만든다. 이게 없으면
    LLM이 매번 비슷한 톤·습관 표현("저도 처음엔…", 정밀한 가짜 통계 등)을
    기본값처럼 반복해서 AI가 쓴 글처럼 보인다.
    """
    if not persona:
        return ""
    name = persona.get("name") or "화자"
    voice = (persona.get("voice") or "").strip()
    quirks = (persona.get("quirks") or "").strip()
    emoji = persona.get("emoji", "0~1개")
    return (
        f"## 이번 화자 프로필 — {name}\n\n"
        f"{voice}\n"
        f"말버릇/습관: {quirks}\n"
        f"이모지 사용량: {emoji} (전체 캡션 기준, 초과 금지)\n\n"
        "카드 문구와 캡션 전체에 이 화자의 말투를 일관되게 녹여라. 다른 화자의\n"
        "말투나 표현(예: 이 프로필에 없는 습관 표현)을 섞지 마라.\n"
    )


def build_system_prompt(structure: dict | None = None,
                        caption_style: dict | None = None,
                        persona: dict | None = None) -> str:
    return "\n".join([
        PROMPT_PREAMBLE,
        _persona_section(persona),
        _story_flow_section(structure),
        _caption_section(caption_style),
        PROMPT_CARD_RULES,
        PROMPT_DM_CTA_RULES,
        PROMPT_ANTI_AI_RULES,
    ])


def call_llm(topic: str,
             recent_topics: list[str] | None = None,
             structure: dict | None = None,
             caption_style: dict | None = None,
             persona: dict | None = None,
             recent_hooks: list[str] | None = None) -> dict:
    """Claude에 카드뉴스 1세트 콘텐츠를 요청."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK가 필요합니다. pip install anthropic")

    avoid = ""
    if recent_topics:
        avoid = "\n이미 이번 주에 다룬 주제(표현/예시 겹치지 마라): " + ", ".join(recent_topics)
    if recent_hooks:
        avoid += (
            "\n최근에 쓴 캡션 첫 줄(hook) — 같은 패턴/구조 반복 금지:\n"
            + "\n".join(f"- {h}" for h in recent_hooks)
        )

    # 카드 수 = 구조에 정의된 길이 (없으면 8~10 범위 안내)
    card_count_hint = "8~10장"
    if structure and structure.get("cards"):
        card_count_hint = f"{len(structure['cards'])}장"

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.getenv("LLM_MODEL", "claude-opus-4-7"),
        max_tokens=4096,
        system=build_system_prompt(structure, caption_style, persona),
        messages=[{
            "role": "user",
            "content": f"서브토픽: {topic}{avoid}\n{card_count_hint}의 카드뉴스 1세트를 위 JSON 스키마로 생성하라.",
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
            "그 자리에서 다음 미팅을 잡고 가는 분들이 확 늘었습니다. 카드에서 풀어볼게요.\n"
            "\n"
            "다만 이건 일반 원칙이라, 고객 성향에 따라 톤을 좀 다르게 가져가야 해요.\n"
            "내 상황엔 어떻게 적용하면 좋을지 궁금하면 '생각해볼게요' 댓글 남기거나 DM 주세요 🙏\n"
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
             "eyebrow": "댓글 · DM",
             "title": "내 케이스엔<em>어떻게</em> 적용할까",
             "subtitle": "고객 성향별로 톤이 달라져요. '생각해볼게요' 댓글 또는 DM 주세요.",
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


DEFAULT_PALETTE = {
    "bg": "#F5F1E8", "ink": "#0A1628", "accent": "#C8A14B", "pop": "#B8321A",
}


# 타이틀에 쓸 수 있는 트렌디 폰트 풀. 값이 None인 file 은 이미 템플릿에
# @font-face 로 선언돼 있는 폰트(Pretendard)라 별도 주입이 필요 없다는 뜻.
# weight 900 으로 등록해서 .title 의 font-weight:900 요청에 그대로 매칭시킨다
# (단일 굵기 디스플레이 폰트라 브라우저의 fake-bold 합성을 피하는 트릭).
FONT_CONFIGS: dict[str, dict] = {
    "pretendard":     {"family": "Pretendard",          "file": None},
    "gmarket_bold":   {"family": "Gmarket Sans",         "file": "GmarketSansBold.otf"},
    "jalnan":         {"family": "Jalnan",               "file": "Jalnan.otf"},
    "bm_dohyeon":     {"family": "BM DOHYEON",           "file": "BMDOHYEON.otf"},
    "bm_hannapro":    {"family": "BM HANNA Pro",         "file": "BMHANNAPro.otf"},
    "bm_euljiro":     {"family": "BM EULJIRO",           "file": "BMEULJIRO.otf"},
    "bm_kiranghaerang": {"family": "BM KIRANGHAERANG",   "file": "BMKIRANGHAERANG.otf"},
    "cookierun":      {"family": "CookieRun",            "file": "CookieRun-Bold.otf"},
    "tmon_monsori":   {"family": "Tmon Monsori",         "file": "TmonMonsori.ttf"},
    "nexon_lv1":      {"family": "NEXON Lv1 Gothic",     "file": "NEXONLv1Gothic-Bold.otf"},
    "cafe24_dangdanghae": {"family": "Cafe24 Dangdanghae", "file": "Cafe24Dangdanghae.otf"},
    "ownglyph_dagyeong": {"family": "Ownglyph_2022_UWY_Da_Gyeong", "file": "OwnglyphDaGyeong.otf"},
}

# newsprint(에디토리얼) 를 뺀 나머지 디자인(dark/gradient/geometric)의 타이틀에
# 순환 배정할 트렌디 폰트 풀. idx(슬롯 회전 인덱스) 기준으로 계속 순환하니까
# 같은 디자인이라도 매번 다른 폰트가 "돌려가며" 쓰인다.
TRENDY_FONT_SLUGS = [k for k in FONT_CONFIGS if k != "pretendard"]


# 새 8 디자인 시스템 (4 base × 2 color variants).
# 각 항목: template 파일 + color (CSS 변수에 주입).
# newsprint(에디토리얼 톤)는 트렌디 폰트가 안 어울려서 항상 Pretendard,
# dark/gradient/geometric(SNS 트렌디 톤) 6장은 title_font 를 고정하지 않고
# TRENDY_FONT_SLUGS 풀에서 idx 로 순환 배정한다 (_title_font_for 참고) —
# 매번 같은 폰트만 나오지 않고 "돌려가며" 쓰이도록.
DESIGN_CONFIGS: dict[str, dict] = {
    "newsprint_forest": {
        "template": "design_newsprint.html",
        "colors": {"bg": "#F8F4EA", "ink": "#181C18",
                   "accent": "#1F4A38", "pop": "#1F4A38"},
    },
    "newsprint_burgundy": {
        "template": "design_newsprint.html",
        "colors": {"bg": "#F5F0E0", "ink": "#201814",
                   "accent": "#7A2740", "pop": "#7A2740"},
    },
    "dark_copper": {
        "template": "design_dark.html",
        "colors": {"bg": "#161618", "ink": "#F0E8DC",
                   "accent": "#C97464", "pop": "#C97464"},
    },
    "dark_sage": {
        "template": "design_dark.html",
        "colors": {"bg": "#1C232D", "ink": "#ECECE4",
                   "accent": "#90A88E", "pop": "#90A88E"},
    },
    "gradient_sunset": {
        "template": "design_gradient.html",
        "colors": {"bg": "#8C3C6E", "ink": "#FAF5EB",
                   "accent": "#FFC864", "pop": "#FFC864",
                   "grad_start": "#E6645A", "grad_end": "#8C3C6E"},
    },
    "gradient_forest": {
        "template": "design_gradient.html",
        "colors": {"bg": "#326964", "ink": "#ECE8DC",
                   "accent": "#D0A85F", "pop": "#D0A85F",
                   "grad_start": "#163832", "grad_end": "#326964"},
    },
    "geometric_earthy": {
        "template": "design_geometric.html",
        "colors": {"bg": "#F0E6D2", "ink": "#2D231C",
                   "accent": "#7A7C41", "pop": "#C0583C",
                   "shape3": "#DCA846"},
    },
    "geometric_navy": {
        "template": "design_geometric.html",
        "colors": {"bg": "#F8F4EC", "ink": "#1C243A",
                   "accent": "#344E82", "pop": "#F58A7A",
                   "shape3": "#FAD7B2"},
    },
}

DESIGN_SLUGS = list(DESIGN_CONFIGS.keys())


def _title_font_for(design_slug: str, idx: int) -> str:
    """디자인 슬러그 + 슬롯 idx → 타이틀 폰트. newsprint 는 항상 Pretendard,
    나머지는 트렌디 폰트 풀을 idx 기준으로 순환."""
    if design_slug.startswith("newsprint"):
        return "pretendard"
    return TRENDY_FONT_SLUGS[idx % len(TRENDY_FONT_SLUGS)]


def render_card_html(
    template: str,
    spec: CardSpec,
    brand_name: str,
    brand_meta: str,
    palette: dict | None = None,
    cover_variant: str = "var-a",
    font_base: str = "",
    title_font: str = "pretendard",
) -> str:
    """아주 단순한 mustache-lite 치환."""
    p = {**DEFAULT_PALETTE, **(palette or {})}
    font_cfg = FONT_CONFIGS.get(title_font) or FONT_CONFIGS["pretendard"]
    title_font_face = ""
    if font_cfg.get("file"):
        title_font_face = (
            f"@font-face {{ font-family: '{font_cfg['family']}'; font-weight: 900; "
            f"src: url('{font_base}/{font_cfg['file']}') format('opentype'); }}"
        )
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
        "COVER_CLASS": f"cover {cover_variant}" if spec.cover else "",
        "COLOR_BG": p["bg"],
        "COLOR_INK": p["ink"],
        "COLOR_ACCENT": p["accent"],
        "COLOR_POP": p["pop"],
        # 신 디자인 (gradient/geometric) 추가 키 — 없는 디자인엔 무해
        "GRAD_START":  p.get("grad_start", p["bg"]),
        "GRAD_END":    p.get("grad_end", p["bg"]),
        "COLOR_SHAPE3": p.get("shape3", p["accent"]),
        "FONT_BASE":   font_base,
        "TITLE_FONT_FAMILY": font_cfg["family"],
        "TITLE_FONT_FACE":   title_font_face,
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

def render_set(
    card_set: CardSet,
    out_dir: Path,
    brand_name: str,
    brand_meta: str,
    palette: dict | None = None,
    cover_variant: str = "var-a",
    template_name: str = "card.html",
    title_font: str = "pretendard",
) -> list[Path]:
    from playwright.sync_api import sync_playwright

    brand_name = (brand_name or "").strip() or "Insurance Talk Notes"
    brand_meta = (brand_meta or "").strip()

    template_path = ROOT / "templates" / template_name
    template = template_path.read_text(encoding="utf-8")
    font_base = (ROOT / "fonts").resolve().as_uri()  # file:///abs/path/cardnews-system/fonts
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    launch_kwargs = {}
    exe = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if exe:
        launch_kwargs["executable_path"] = exe

    # page.set_content() 로 주입한 문서는 origin 이 about:blank 취급돼서
    # Chromium 이 file:// 로컬 폰트 로드를 통째로 막는다 ("Not allowed to load
    # local resource"). @font-face 가 전부 조용히 실패하고 시스템 기본 산세리프로
    # 폴백돼도 화면상 "그럴싸하게" 보여서 눈치채기 어렵다 — document.fonts 로
    # 직접 확인해서 잡음. file:// 문서로 실제 navigate 하면 같은 file:// origin
    # 리소스 로드가 허용되므로, 카드마다 임시 html 파일에 써서 page.goto() 한다.
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".html")
    os.close(tmp_fd)
    tmp_html = Path(tmp_name)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            # 1080×1350 (인스타 portrait 4:5)
            context = browser.new_context(viewport={"width": 1080, "height": 1350},
                                          device_scale_factor=2)
            page = context.new_page()
            for spec in card_set.cards:
                html = render_card_html(
                    template, spec, brand_name, brand_meta,
                    palette=palette, cover_variant=cover_variant, font_base=font_base,
                    title_font=title_font,
                )
                tmp_html.write_text(html, encoding="utf-8")
                page.goto(tmp_html.resolve().as_uri(), wait_until="networkidle")
                png_path = out_dir / f"{spec.page:02d}.png"
                page.screenshot(path=str(png_path), full_page=False, omit_background=False,
                                clip={"x": 0, "y": 0, "width": 1080, "height": 1350})
                paths.append(png_path)
            browser.close()
    finally:
        tmp_html.unlink(missing_ok=True)
    return paths


def _design_for_topic(cfg: dict, topic: str) -> str:
    """토픽 인덱스 % 8 로 8 디자인 결정적 회전. 같은 토픽은 항상 같은 디자인."""
    return DESIGN_SLUGS[_topic_index(cfg, topic) % len(DESIGN_SLUGS)]


# ----------------------------- CLI ---------------------------------------

def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def slot_dir(base: Path, day: dt.date, slot: str) -> Path:
    return base / day.isoformat() / slot / "draft"


def _topic_index(cfg: dict, topic: str) -> int:
    """토픽 풀에서의 인덱스. 풀에 없으면 해시 기반 결정적 인덱스."""
    pool = cfg.get("content", {}).get("topics") or []
    if topic in pool:
        return pool.index(topic)
    return abs(hash(topic))


def _palette_for_topic(cfg: dict, topic: str) -> dict | None:
    palettes = cfg.get("design", {}).get("palettes") or []
    if not palettes:
        return None
    return palettes[_topic_index(cfg, topic) % len(palettes)]


def _cover_variant_for_topic(cfg: dict, topic: str) -> str:
    """variant 는 palette 가 한 바퀴 다 돈 뒤에야 바뀐다.
    이렇게 해야 (palette, variant) 조합이 lcm 에 묶여 일찍 반복하는 걸 막고
    palettes×variants 만큼의 unique 조합을 토픽 수만큼 순서대로 소진한다.
    """
    variants = cfg.get("design", {}).get("cover_variants") or ["var-a"]
    palettes = cfg.get("design", {}).get("palettes") or []
    np = max(len(palettes), 1)
    return variants[(_topic_index(cfg, topic) // np) % len(variants)]


def _structure_for_idx(cfg: dict, idx: int) -> dict | None:
    """슬롯 idx → 스토리 구조 1개. config 에 없으면 None (레거시 폴백)."""
    structures = cfg.get("content", {}).get("story_structures") or []
    if not structures:
        return None
    return structures[idx % len(structures)]


def _caption_style_for_idx(cfg: dict, idx: int) -> dict | None:
    styles = cfg.get("content", {}).get("caption_styles") or []
    if not styles:
        return None
    return styles[idx % len(styles)]


def _persona_for_idx(cfg: dict, idx: int) -> dict | None:
    """슬롯 idx → 화자 페르소나 1개."""
    personas = cfg.get("content", {}).get("writer_personas") or []
    if not personas:
        return None
    return personas[idx % len(personas)]


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
    structure: dict | None = None,
    caption_style: dict | None = None,
    persona: dict | None = None,
    recent_hooks: list[str] | None = None,
) -> CardSet:
    if use_llm:
        payload = call_llm(topic, recent_topics=recent_topics,
                           structure=structure, caption_style=caption_style,
                           persona=persona, recent_hooks=recent_hooks)
    else:
        payload = dummy_payload(topic, idx=idx)
    card_set = payload_to_set(payload)
    if not card_set.topic:
        card_set.topic = topic

    out_base = ROOT / cfg["paths"]["output"]
    out = slot_dir(out_base, day, slot)

    # 신 8 디자인 시스템 — 토픽별 결정적 회전
    design_slug = _design_for_topic(cfg, topic)
    design = DESIGN_CONFIGS[design_slug]
    title_font = _title_font_for(design_slug, idx)

    render_set(
        card_set, out, cfg["brand"]["name"], cfg["brand"]["meta"],
        palette=design["colors"],
        cover_variant="",  # 신 디자인은 cover variant 사용 안 함 (디자인 자체가 변주)
        template_name=design["template"],
        title_font=title_font,
    )

    write_caption(ROOT / cfg["paths"]["captions"], day, slot, card_set.caption)
    print(f"[OK] {day} {slot}  {len(card_set.cards)}장 ({topic}) "
          f"[design={design_slug} font={title_font}] → {out}")
    return card_set


def _resolve_topics(args, cfg: dict) -> list[str]:
    if args.topic:
        return [args.topic]
    pool = cfg.get("content", {}).get("topics") or []
    if pool:
        return pool
    return [cfg.get("content", {}).get("default_topic", "보험 상담 화법")]


# 토픽 회전 인덱스를 (날짜, 슬롯) 으로부터 결정적으로 계산.
# 이전 구현은 main() 안에서 idx=0 으로 초기화 후 슬롯마다 +1 했는데,
# schedule cron 이 매일 --days 1 로 호출되므로 매 실행이 idx=0 부터 시작
# → AM=topics[0], NOON=topics[1], PM=topics[2] 가 매일 반복.
# 날짜 기반으로 idx 를 산출하면 슬롯당 len(topics) 일 주기로 순환한다.
SLOT_ORDER = ["AM", "NOON", "PM"]
ROTATION_EPOCH = dt.date(2026, 1, 1)


def _rotation_idx(day: dt.date, slot: str) -> int:
    slot_pos = SLOT_ORDER.index(slot) if slot in SLOT_ORDER else 0
    return (day - ROTATION_EPOCH).days * len(SLOT_ORDER) + slot_pos


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
    parser.add_argument("--slots", default="AM,NOON,PM",
                        help="콤마구분: AM,NOON,PM")
    parser.add_argument("--start", default=None, help="시작일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--preview", action="store_true", help="오늘 AM 한 세트만 생성")
    parser.add_argument("--no-llm", action="store_true", help="더미 페이로드로 동작 확인")
    parser.add_argument("--skip-existing", action="store_true",
                        help="captions/<date>_<slot>.txt 가 이미 있으면 그 slot 생성 skip "
                             "(daily cron 의 idempotency)")
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
    captions_dir = ROOT / cfg["paths"]["captions"]

    # recent_topics/recent_hooks 로 LLM 에 직전 슬롯들 정보를 알려주기. used 는
    # 이번 런에서 실제로 만든 것만 들어가므로, daily cron(1일치) 에선 첫 슬롯이
    # 빈 hint 로 들어간다. 시드용으로 지난 6슬롯만큼은 회전 인덱스를 역산해
    # 토픽을 채우고, 이미 생성돼 있는 캡션 파일이 있으면 첫 줄(hook) 도 읽어
    # "같은 후킹 패턴 반복" 을 막는 데 쓴다.
    used: list[str] = []
    used_hooks: list[str] = []
    seed_pairs: list[tuple[dt.date, str]] = []
    for offset in range(1, 3):
        d_seed = today - dt.timedelta(days=offset)
        for slot in reversed(SLOT_ORDER):
            seed_pairs.append((d_seed, slot))
    seed_pairs = list(reversed(seed_pairs))[-6:]
    for d_seed, slot_seed in seed_pairs:
        used.append(topics[_rotation_idx(d_seed, slot_seed) % len(topics)])
        seed_cap = captions_dir / f"{d_seed.isoformat()}_{slot_seed}.txt"
        if seed_cap.exists():
            first_line = seed_cap.read_text(encoding="utf-8").strip().splitlines()
            if first_line:
                used_hooks.append(first_line[0])

    for d in range(args.days):
        day = today + dt.timedelta(days=d)
        for slot in slots:
            if args.skip_existing:
                cap_path = captions_dir / f"{day.isoformat()}_{slot}.txt"
                if cap_path.exists():
                    print(f"[skip] {day} {slot}: caption already exists", file=sys.stderr)
                    continue
            idx = _rotation_idx(day, slot)
            topic = topics[idx % len(topics)]
            structure = _structure_for_idx(cfg, idx)
            caption_style = _caption_style_for_idx(cfg, idx)
            persona = _persona_for_idx(cfg, idx)
            print(
                f"[gen] {day} {slot} topic={topic!r} "
                f"structure={(structure or {}).get('name','legacy')} "
                f"caption={(caption_style or {}).get('name','legacy')} "
                f"persona={(persona or {}).get('name','legacy')}",
                file=sys.stderr,
            )
            card_set = generate_one(
                topic, day, slot, cfg, use_llm=use_llm, idx=idx,
                recent_topics=used[-6:] or None,
                structure=structure, caption_style=caption_style,
                persona=persona, recent_hooks=used_hooks[-6:] or None,
            )
            used.append(topic)
            first_line = (card_set.caption or "").strip().splitlines()
            if first_line:
                used_hooks.append(first_line[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
