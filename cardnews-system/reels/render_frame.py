"""
손글씨 노트풍 릴스 정적 프레임 렌더러.

Phase 1: 단일 프레임 렌더링 (1080x1920 PNG). 디자인 검증용.
Phase 2 에서 시간축 매개변수(t)를 받아 애니메이션 프레임 생성으로 확장한다.

레이아웃은 README 의 스펙(2번 디자인 시스템)을 따른다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "fonts"

W, H = 1080, 1920

# 컬러 팔레트 (스펙 그대로)
PAPER     = (250, 246, 235)
LINE      = (210, 220, 230)
INK       = (32, 40, 70)
RED_INK   = (210, 60, 70)
PENCIL    = (110, 118, 135)
HL_YELLOW = (255, 230, 60)
STICKY    = (255, 232, 110)
STICKY_SHADOW = (200, 180, 60)


@dataclass
class ReelSpec:
    """릴스 1개 분량의 텍스트."""
    category: str          # 우상단 포스트잇 ("어린이보험", "연금" 등)
    headline: list[str]    # 대제목 2줄 (페인포인트 질문형)
    body: list[str]        # 본문 2줄
    quote: list[str]       # 회색 인용 2줄 ("'생각해볼게요'" 같은)
    transition: str = "근데..."
    red_lines: list[str] = field(default_factory=list)  # 빨간 펜 핵심 2줄
    checklist: list[str] = field(default_factory=list)  # 5개 (마지막 1~2개 형광펜)
    highlight_checks: int = 2                            # 끝에서 N개 형광펜 강조
    bonus: str = ""                                      # 별 옆 한 줄
    cta: str = ""                                        # 하단 포스트잇


# ----------------------------- 폰트 -----------------------------------------

def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def f_body(size: int = 60) -> ImageFont.FreeTypeFont:
    return _font("NanumPenScript-Regular.ttf", size)


def f_title(size: int = 90) -> ImageFont.FreeTypeFont:
    return _font("GamjaFlower-Regular.ttf", size)


def f_pencil(size: int = 56) -> ImageFont.FreeTypeFont:
    return _font("HiMelody-Regular.ttf", size)


# ----------------------------- 배경 -----------------------------------------

def draw_notepaper(img: Image.Image) -> None:
    """노트지: 가로 줄 + 좌측 빨간 마진선 + 펀치홀 4개."""
    d = ImageDraw.Draw(img)
    # 가로 줄 (90px 간격)
    for y in range(180, H, 90):
        d.line([(60, y), (W - 30, y)], fill=LINE, width=2)
    # 좌측 마진 빨간 세로선
    d.line([(140, 60), (140, H - 60)], fill=(220, 140, 150), width=3)
    # 펀치홀 4개 (좌측, 세로 균등)
    for i, y in enumerate([260, 760, 1260, 1760]):
        d.ellipse([(40, y), (90, y + 50)], fill=(225, 225, 230), outline=(180, 180, 188), width=2)


def draw_sticky_top(img: Image.Image, text: str) -> None:
    """우상단 노란 포스트잇 (살짝 기울임)."""
    # 별도 레이어에 그려서 회전
    note_w, note_h = 360, 130
    layer = Image.new("RGBA", (note_w + 40, note_h + 40), (0, 0, 0, 0))
    nd = ImageDraw.Draw(layer)
    # 그림자
    nd.polygon([(28, 24), (note_w + 28, 22), (note_w + 32, note_h + 26), (24, note_h + 30)],
               fill=(0, 0, 0, 60))
    # 포스트잇 본체
    nd.polygon([(20, 18), (note_w + 20, 16), (note_w + 24, note_h + 20), (16, note_h + 24)],
               fill=STICKY)
    # 텍스트
    font = f_title(56)
    bbox = nd.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    nd.text((20 + (note_w - tw) // 2, 18 + (note_h - th) // 2 - bbox[1]),
            text, fill=INK, font=font)
    layer = layer.rotate(-6, resample=Image.BICUBIC, expand=True)
    img.paste(layer, (W - layer.width - 50, 60), layer)


def draw_sticky_bottom(img: Image.Image, text: str) -> None:
    """하단 노란 포스트잇 CTA."""
    note_w, note_h = 880, 220
    layer = Image.new("RGBA", (note_w + 60, note_h + 60), (0, 0, 0, 0))
    nd = ImageDraw.Draw(layer)
    nd.polygon([(34, 36), (note_w + 34, 30), (note_w + 38, note_h + 38), (30, note_h + 44)],
               fill=(0, 0, 0, 70))
    nd.polygon([(24, 26), (note_w + 24, 20), (note_w + 28, note_h + 28), (20, note_h + 34)],
               fill=STICKY)
    # 좌측 빨간 동그라미 (강조 도트)
    nd.ellipse([(60, 86), (140, 166)], outline=RED_INK, width=6)
    nd.text((75, 96), "!", fill=RED_INK, font=f_title(72))
    # CTA 텍스트
    font = f_title(64)
    nd.text((180, 60), text, fill=INK, font=font)
    layer = layer.rotate(1.5, resample=Image.BICUBIC, expand=True)
    img.paste(layer, ((W - layer.width) // 2, 1690), layer)


# ----------------------------- 손그림 요소 -------------------------------------

def hl_stroke(img: Image.Image, x0: int, y0: int, x1: int, y1: int,
              progress: float = 1.0, thickness: int = 50) -> None:
    """노란 형광펜 스트로크 (반투명, 거친 가장자리, 좌→우)."""
    if progress <= 0:
        return
    end_x = int(x0 + (x1 - x0) * min(1.0, progress))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # 중심선 살짝 위아래로 들쭉날쭉
    cy = (y0 + y1) // 2
    half = thickness // 2
    # 메인 막대
    ld.rectangle([(x0, cy - half), (end_x, cy + half)],
                 fill=(*HL_YELLOW, 130))
    # 가장자리 거칠게: 위/아래 페더링용 약한 라인 몇 줄
    for dy in range(-half - 4, -half):
        ld.line([(x0 + 4, cy + dy), (end_x - 4, cy + dy)],
                fill=(*HL_YELLOW, 70), width=1)
    for dy in range(half, half + 4):
        ld.line([(x0 + 4, cy + dy), (end_x - 4, cy + dy)],
                fill=(*HL_YELLOW, 70), width=1)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=1.2))
    img.alpha_composite(layer)


def red_wavy_underline(img: Image.Image, x0: int, y: int, x1: int,
                       progress: float = 1.0) -> None:
    """빨간 펜 물결 밑줄 (사인파 + 지터)."""
    if progress <= 0:
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    end_x = int(x0 + (x1 - x0) * min(1.0, progress))
    pts = []
    rnd = random.Random(int(y))
    for x in range(x0, end_x, 3):
        wave = math.sin((x - x0) * 0.06) * 6
        jitter = rnd.uniform(-1.2, 1.2)
        pts.append((x, y + wave + jitter))
    if len(pts) >= 2:
        ld.line(pts, fill=(*RED_INK, 235), width=5, joint="curve")
    img.alpha_composite(layer)


def hand_arrow_down(img: Image.Image, x: int, y0: int, y1: int,
                    progress: float = 1.0) -> None:
    """손그림 화살표 ↓ (살짝 흔들리는 선 + 화살촉)."""
    if progress <= 0:
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    end_y = int(y0 + (y1 - y0) * min(1.0, progress))
    rnd = random.Random(x * 31)
    pts = []
    for yy in range(y0, end_y, 4):
        jx = rnd.uniform(-2.0, 2.0)
        pts.append((x + jx, yy))
    if len(pts) >= 2:
        ld.line(pts, fill=INK, width=4, joint="curve")
    # 화살촉 (progress 95% 이상일 때만)
    if progress > 0.9:
        ld.line([(x - 18, end_y - 24), (x, end_y), (x + 18, end_y - 24)],
                fill=INK, width=4, joint="curve")
    img.alpha_composite(layer)


def hand_checkbox(img: Image.Image, x: int, y: int, checked: bool,
                  check_progress: float = 1.0, size: int = 46) -> None:
    """손그림 체크박스 + (옵션) 빨간 체크."""
    d = ImageDraw.Draw(img)
    # 박스: 살짝 비뚤어진 사각형
    pts = [(x, y), (x + size, y - 2), (x + size + 2, y + size), (x - 1, y + size - 1)]
    d.polygon(pts, outline=INK)
    # 두께 보강
    for i in range(len(pts)):
        d.line([pts[i], pts[(i + 1) % len(pts)]], fill=INK, width=3)
    if checked and check_progress > 0:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        # 빨간 체크: 짧은 선 + 긴 선
        a = (x + 8, y + size // 2 + 2)
        b = (x + size // 2 - 2, y + size - 6)
        c = (x + size + 12, y - 6)
        # 0~50%: a→b, 50~100%: b→c
        if check_progress < 0.5:
            t = check_progress * 2
            bx = a[0] + (b[0] - a[0]) * t
            by = a[1] + (b[1] - a[1]) * t
            ld.line([a, (bx, by)], fill=RED_INK, width=5, joint="curve")
        else:
            t = (check_progress - 0.5) * 2
            cx = b[0] + (c[0] - b[0]) * t
            cy = b[1] + (c[1] - b[1]) * t
            ld.line([a, b], fill=RED_INK, width=5, joint="curve")
            ld.line([b, (cx, cy)], fill=RED_INK, width=5, joint="curve")
        img.alpha_composite(layer)


def hand_star(img: Image.Image, cx: int, cy: int, progress: float = 1.0,
              size: int = 38) -> None:
    """별 낙서 (5각, 살짝 비뚤)."""
    if progress <= 0:
        return
    rnd = random.Random(cx + cy)
    pts = []
    for i in range(11):
        angle = -math.pi / 2 + i * math.pi / 5
        r = size if i % 2 == 0 else size * 0.42
        jx = rnd.uniform(-2, 2)
        jy = rnd.uniform(-2, 2)
        pts.append((cx + math.cos(angle) * r + jx, cy + math.sin(angle) * r + jy))
    # progress 만큼만 그리기
    n = max(2, int(len(pts) * progress))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.line(pts[:n] + [pts[0]] if n >= len(pts) else pts[:n],
            fill=RED_INK, width=4, joint="curve")
    img.alpha_composite(layer)


# ----------------------------- 텍스트 헬퍼 -----------------------------------

def draw_text_centered(d: ImageDraw.ImageDraw, x: int, y: int, text: str,
                       font: ImageFont.FreeTypeFont, fill, max_width: int = None) -> tuple[int, int]:
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    if max_width and tw > max_width:
        # 그냥 자르지 않고 그대로. caller 가 줄을 나눠야 함.
        pass
    d.text((x - bbox[0], y - bbox[1]), text, fill=fill, font=font)
    return tw, bbox[3] - bbox[1]


# ----------------------------- 한 프레임 렌더 -----------------------------------

def render_frame(spec: ReelSpec, t: float = 1.0) -> Image.Image:
    """전체 노출 시점(t=1.0) 한 프레임. Phase 2 에서 t 별 부분 노출 추가."""
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    draw_notepaper(img)

    d = ImageDraw.Draw(img)

    # 우상단 포스트잇 태그 (카테고리)
    draw_sticky_top(img, spec.category)

    # 대제목 2줄 (y=200~340)
    title_font = f_title(96)
    for i, line in enumerate(spec.headline[:2]):
        y = 230 + i * 110
        d.text((170, y), line, fill=INK, font=title_font)

    # 본문 2줄 + 형광펜 강조 (y=460~580)
    body_font = f_body(64)
    for i, line in enumerate(spec.body[:2]):
        y = 480 + i * 90
        if i == 0:
            # 첫 줄에 형광펜
            d.text((180, y), line, fill=INK, font=body_font)
            bbox = d.textbbox((180, y), line, font=body_font)
            hl_stroke(img, bbox[0] - 6, bbox[1] - 4, bbox[2] + 10, bbox[3] + 6,
                      progress=1.0, thickness=58)
            # 형광펜이 텍스트를 덮지 않게 위에 다시
            d.text((180, y), line, fill=INK, font=body_font)
        else:
            d.text((180, y), line, fill=INK, font=body_font)

    # 인용 2줄 (회색, y=680~760)
    quote_font = f_pencil(56)
    for i, line in enumerate(spec.quote[:2]):
        y = 690 + i * 75
        d.text((200, y), line, fill=PENCIL, font=quote_font)

    # 화살표 + "근데..." (y=820)
    hand_arrow_down(img, 540, 830, 920, progress=1.0)
    d.text((600, 850), spec.transition, fill=INK, font=f_title(64))

    # 빨간 펜 핵심 2줄 + 물결 밑줄 (y=940~1170)
    red_font = f_title(78)
    for i, line in enumerate(spec.red_lines[:2]):
        y = 960 + i * 100
        d.text((180, y), line, fill=RED_INK, font=red_font)
        bbox = d.textbbox((180, y), line, font=red_font)
        red_wavy_underline(img, bbox[0] - 4, bbox[3] + 12, bbox[2] + 8, progress=1.0)

    # 체크리스트 5개 (y=1230~1560)
    check_font = f_body(58)
    n = min(5, len(spec.checklist))
    start_y = 1260
    for i in range(n):
        y = start_y + i * 66
        is_highlighted = i >= n - spec.highlight_checks
        hand_checkbox(img, 180, y - 8, checked=True, check_progress=1.0, size=44)
        d.text((250, y - 14), spec.checklist[i], fill=INK, font=check_font)
        if is_highlighted:
            bbox = d.textbbox((250, y - 14), spec.checklist[i], font=check_font)
            hl_stroke(img, bbox[0] - 4, bbox[1] - 2, bbox[2] + 8, bbox[3] + 4,
                      progress=1.0, thickness=46)
            d.text((250, y - 14), spec.checklist[i], fill=INK, font=check_font)

    # 별 + 보너스 한 줄 (y=1580~1660)
    if spec.bonus:
        hand_star(img, 200, 1610, progress=1.0, size=34)
        d.text((250, 1580), spec.bonus, fill=INK, font=f_body(54))

    # 하단 포스트잇 CTA
    if spec.cta:
        draw_sticky_bottom(img, spec.cta)

    return img.convert("RGB")


# ----------------------------- 데모 -----------------------------------------

def demo_spec() -> ReelSpec:
    """디자인 검증용 샘플. 어린이보험 페르소나 톤."""
    return ReelSpec(
        category="어린이보험",
        headline=[
            "아이 보험,",
            "이거 빠지면 후회해요",
        ],
        body=[
            "통원치료비 1일 한도",
            "확인 안 하면 진짜 큰일",
        ],
        quote=[
            "“그냥 다 보장된다고 하셔서요…”",
            "라고 하시는 분 많아요",
        ],
        red_lines=[
            "보장 ‘되는 것’이 아니라",
            "‘얼마’ 받느냐가 핵심",
        ],
        checklist=[
            "통원 1일 한도",
            "입원 1일 한도 + 면책기간",
            "수술비 등급별 금액",
            "특정 질병 진단비",
            "면책·감액 조항",
        ],
        highlight_checks=2,
        bonus="*보장은 가입 조건·약관 따라 달라져요",
        cta="댓글 ‘정보’ 남기면 DM 드려요",
    )


if __name__ == "__main__":
    import sys
    spec = demo_spec()
    img = render_frame(spec, t=1.0)
    out = ROOT / "samples" / "phase1_static.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print(f"saved {out} ({out.stat().st_size // 1024} KB)")
