"""
손글씨 노트풍 릴스 프레임 렌더러.

render_frame(spec, t_sec) 가 단일 진입점.
- t_sec >= 15.0: 모든 요소 풀 노출 (정적)
- t_sec < 15.0: 타임라인 따라 부분 노출 (애니메이션)

타임라인 (스펙 §3, ease_out_cubic):
  0.3~1.0  포스트잇 상단 슬라이드인 + 페이드
  0.9~2.1  대제목 2줄 순차 페이드
  2.3~3.1  본문 2줄 페이드
  3.1~3.7  본문 형광펜 좌→우
  3.7~4.5  인용 2줄 페이드
  4.6~5.4  화살표 + "근데..." 페이드
  5.4~6.6  빨간 펜 핵심 2줄 페이드
  6.6~7.2  빨간 물결 밑줄 좌→우
  7.2~9.3  체크박스 5개 순차 (0.35s 간격)
  9.2~9.8  별 + 보너스
  9.9~10.9 하단 포스트잇 슬라이드인
  11~15    홀드
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

PAPER     = (250, 246, 235)
LINE      = (210, 220, 230)
INK       = (32, 40, 70)
RED_INK   = (210, 60, 70)
PENCIL    = (110, 118, 135)
HL_YELLOW = (255, 230, 60)
STICKY    = (255, 232, 110)


@dataclass
class ReelSpec:
    category: str
    headline: list[str]
    body: list[str]
    quote: list[str]
    transition: str = "근데..."
    red_lines: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    highlight_checks: int = 2
    bonus: str = ""
    cta: str = ""


# ----------------------------- 타이밍 -------------------------------------

def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def prog(t_sec: float, start: float, end: float) -> float:
    if t_sec < start:
        return 0.0
    if t_sec >= end:
        return 1.0
    return ease_out_cubic((t_sec - start) / (end - start))


# ----------------------------- 폰트 ---------------------------------------

def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def f_body(size: int = 60) -> ImageFont.FreeTypeFont:
    return _font("NanumPenScript-Regular.ttf", size)


def f_title(size: int = 90) -> ImageFont.FreeTypeFont:
    return _font("GamjaFlower-Regular.ttf", size)


def f_pencil(size: int = 56) -> ImageFont.FreeTypeFont:
    return _font("HiMelody-Regular.ttf", size)


# ----------------------------- 배경 ---------------------------------------

def draw_notepaper(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    for y in range(180, H, 90):
        d.line([(60, y), (W - 30, y)], fill=LINE, width=2)
    d.line([(140, 60), (140, H - 60)], fill=(220, 140, 150), width=3)
    for y in [260, 760, 1260, 1760]:
        d.ellipse([(40, y), (90, y + 50)], fill=(225, 225, 230), outline=(180, 180, 188), width=2)


def _sticky_layer(text: str, font_size: int, w: int, h: int, rotate_deg: float,
                  decorate_dot: bool = False) -> Image.Image:
    layer = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    nd = ImageDraw.Draw(layer)
    # 그림자
    nd.polygon([(28, 24), (w + 28, 22), (w + 32, h + 26), (24, h + 30)],
               fill=(0, 0, 0, 60))
    # 본체
    nd.polygon([(20, 18), (w + 20, 16), (w + 24, h + 20), (16, h + 24)],
               fill=STICKY)
    if decorate_dot:
        nd.ellipse([(60, h // 2 + 20), (140, h // 2 + 100)], outline=RED_INK, width=6)
        nd.text((75, h // 2 + 30), "!", fill=RED_INK, font=f_title(72))
        nd.text((180, h // 2 - 50), text, fill=INK, font=f_title(font_size))
    else:
        font = f_title(font_size)
        bbox = nd.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        nd.text((20 + (w - tw) // 2, 18 + (h - th) // 2 - bbox[1]),
                text, fill=INK, font=font)
    return layer.rotate(rotate_deg, resample=Image.BICUBIC, expand=True)


def draw_sticky_top(img: Image.Image, text: str, progress: float = 1.0) -> None:
    if progress <= 0:
        return
    layer = _sticky_layer(text, 56, 360, 130, -6)
    if progress < 1.0:
        # 알파 조정
        alpha = layer.split()[-1].point(lambda a: int(a * progress))
        layer.putalpha(alpha)
    offset_y = int((1 - progress) * 50)  # 아래→위 슬라이드
    img.alpha_composite(layer, (W - layer.width - 50, 60 + offset_y))


def draw_sticky_bottom(img: Image.Image, text: str, progress: float = 1.0) -> None:
    if progress <= 0:
        return
    layer = _sticky_layer(text, 64, 880, 220, 1.5, decorate_dot=True)
    if progress < 1.0:
        alpha = layer.split()[-1].point(lambda a: int(a * progress))
        layer.putalpha(alpha)
    offset_y = int((1 - progress) * 80)  # 아래에서 위로 슬라이드인
    img.alpha_composite(layer, ((W - layer.width) // 2, 1690 + offset_y))


# ----------------------------- 손그림 -------------------------------------

def hl_stroke(img: Image.Image, x0: int, y0: int, x1: int, y1: int,
              progress: float = 1.0, thickness: int = 50) -> None:
    if progress <= 0:
        return
    end_x = int(x0 + (x1 - x0) * min(1.0, progress))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    cy = (y0 + y1) // 2
    half = thickness // 2
    ld.rectangle([(x0, cy - half), (end_x, cy + half)],
                 fill=(*HL_YELLOW, 130))
    for dy in range(-half - 4, -half):
        ld.line([(x0 + 4, cy + dy), (end_x - 4, cy + dy)], fill=(*HL_YELLOW, 70), width=1)
    for dy in range(half, half + 4):
        ld.line([(x0 + 4, cy + dy), (end_x - 4, cy + dy)], fill=(*HL_YELLOW, 70), width=1)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=1.2))
    img.alpha_composite(layer)


def red_wavy_underline(img: Image.Image, x0: int, y: int, x1: int,
                       progress: float = 1.0) -> None:
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
    if progress > 0.9:
        ld.line([(x - 18, end_y - 24), (x, end_y), (x + 18, end_y - 24)],
                fill=INK, width=4, joint="curve")
    img.alpha_composite(layer)


def hand_checkbox(img: Image.Image, x: int, y: int, checked: bool,
                  check_progress: float = 1.0, size: int = 46) -> None:
    d = ImageDraw.Draw(img)
    pts = [(x, y), (x + size, y - 2), (x + size + 2, y + size), (x - 1, y + size - 1)]
    for i in range(len(pts)):
        d.line([pts[i], pts[(i + 1) % len(pts)]], fill=INK, width=3)
    if checked and check_progress > 0:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        a = (x + 8, y + size // 2 + 2)
        b = (x + size // 2 - 2, y + size - 6)
        c = (x + size + 12, y - 6)
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
    n = max(2, int(len(pts) * progress))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    if n >= len(pts):
        ld.line(pts + [pts[0]], fill=RED_INK, width=4, joint="curve")
    else:
        ld.line(pts[:n], fill=RED_INK, width=4, joint="curve")
    img.alpha_composite(layer)


# ----------------------------- 텍스트 ------------------------------------

def draw_text(img: Image.Image, x: int, y: int, text: str,
              font: ImageFont.FreeTypeFont, fill_rgb: tuple, alpha: float = 1.0) -> None:
    if alpha <= 0 or not text:
        return
    if alpha >= 1.0:
        ImageDraw.Draw(img).text((x, y), text, fill=fill_rgb, font=font)
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x, y), text, fill=fill_rgb + (int(255 * alpha),), font=font)
    img.alpha_composite(layer)


# ----------------------------- 메인 렌더 -----------------------------------

def render_frame(spec: ReelSpec, t_sec: float = 15.0) -> Image.Image:
    """t_sec 시점의 1080×1920 RGB 프레임."""
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    draw_notepaper(img)

    # 타임라인 진행률
    p_top      = prog(t_sec, 0.3, 1.0)
    p_h1       = prog(t_sec, 0.9, 1.5)
    p_h2       = prog(t_sec, 1.5, 2.1)
    p_b1       = prog(t_sec, 2.3, 2.7)
    p_b2       = prog(t_sec, 2.7, 3.1)
    p_hl       = prog(t_sec, 3.1, 3.7)
    p_q1       = prog(t_sec, 3.7, 4.1)
    p_q2       = prog(t_sec, 4.1, 4.5)
    p_arrow    = prog(t_sec, 4.6, 5.4)
    p_trans    = prog(t_sec, 4.9, 5.4)
    p_r1       = prog(t_sec, 5.4, 6.0)
    p_r2       = prog(t_sec, 6.0, 6.6)
    p_rwave    = prog(t_sec, 6.6, 7.2)
    p_check    = [prog(t_sec, 7.2 + i * 0.35, 7.2 + i * 0.35 + 0.5) for i in range(5)]
    p_star     = prog(t_sec, 9.2, 9.5)
    p_bonus    = prog(t_sec, 9.4, 9.8)
    p_bot      = prog(t_sec, 9.9, 10.9)

    draw_sticky_top(img, spec.category, progress=p_top)

    # 대제목 2줄
    title_font = f_title(96)
    if len(spec.headline) >= 1:
        draw_text(img, 170, 230, spec.headline[0], title_font, INK, p_h1)
    if len(spec.headline) >= 2:
        draw_text(img, 170, 340, spec.headline[1], title_font, INK, p_h2)

    # 본문 2줄 + 형광펜
    body_font = f_body(64)
    if len(spec.body) >= 1:
        draw_text(img, 180, 480, spec.body[0], body_font, INK, p_b1)
        if p_hl > 0:
            bbox = ImageDraw.Draw(img).textbbox((180, 480), spec.body[0], font=body_font)
            hl_stroke(img, bbox[0] - 6, bbox[1] - 4, bbox[2] + 10, bbox[3] + 6,
                      progress=p_hl, thickness=58)
            draw_text(img, 180, 480, spec.body[0], body_font, INK, p_b1)
    if len(spec.body) >= 2:
        draw_text(img, 180, 570, spec.body[1], body_font, INK, p_b2)

    # 인용
    quote_font = f_pencil(56)
    if len(spec.quote) >= 1:
        draw_text(img, 200, 690, spec.quote[0], quote_font, PENCIL, p_q1)
    if len(spec.quote) >= 2:
        draw_text(img, 200, 765, spec.quote[1], quote_font, PENCIL, p_q2)

    # 화살표 + 전환
    hand_arrow_down(img, 540, 830, 920, progress=p_arrow)
    draw_text(img, 600, 850, spec.transition, f_title(64), INK, p_trans)

    # 빨간 핵심
    red_font = f_title(78)
    if len(spec.red_lines) >= 1:
        draw_text(img, 180, 960, spec.red_lines[0], red_font, RED_INK, p_r1)
        if p_rwave > 0:
            bbox = ImageDraw.Draw(img).textbbox((180, 960), spec.red_lines[0], font=red_font)
            red_wavy_underline(img, bbox[0] - 4, bbox[3] + 12, bbox[2] + 8, progress=p_rwave)
    if len(spec.red_lines) >= 2:
        draw_text(img, 180, 1060, spec.red_lines[1], red_font, RED_INK, p_r2)
        if p_rwave > 0:
            bbox = ImageDraw.Draw(img).textbbox((180, 1060), spec.red_lines[1], font=red_font)
            red_wavy_underline(img, bbox[0] - 4, bbox[3] + 12, bbox[2] + 8, progress=p_rwave)

    # 체크리스트
    check_font = f_body(58)
    n = min(5, len(spec.checklist))
    for i in range(n):
        pc = p_check[i]
        if pc <= 0:
            continue
        y = 1260 + i * 66
        is_hl = i >= n - spec.highlight_checks
        if pc > 0.4:
            hand_checkbox(img, 180, y - 8, checked=True,
                          check_progress=min(1.0, (pc - 0.4) / 0.6), size=44)
        else:
            hand_checkbox(img, 180, y - 8, checked=False, size=44)
        draw_text(img, 250, y - 14, spec.checklist[i], check_font, INK, pc)
        if is_hl and pc >= 1.0:
            bbox = ImageDraw.Draw(img).textbbox((250, y - 14), spec.checklist[i], font=check_font)
            hl_stroke(img, bbox[0] - 4, bbox[1] - 2, bbox[2] + 8, bbox[3] + 4,
                      progress=1.0, thickness=46)
            draw_text(img, 250, y - 14, spec.checklist[i], check_font, INK, 1.0)

    # 별 + 보너스
    hand_star(img, 200, 1610, progress=p_star, size=34)
    draw_text(img, 250, 1580, spec.bonus, f_body(54), INK, p_bonus)

    # 하단 포스트잇
    draw_sticky_bottom(img, spec.cta, progress=p_bot)

    return img.convert("RGB")


# ----------------------------- 데모 ---------------------------------------

def demo_spec() -> ReelSpec:
    return ReelSpec(
        category="어린이보험",
        headline=["아이 보험,", "이거 빠지면 후회해요"],
        body=["통원치료비 1일 한도", "확인 안 하면 진짜 큰일"],
        quote=["“그냥 다 보장된다고 하셔서요…”", "라고 하시는 분 많아요"],
        red_lines=["보장 ‘되는 것’이 아니라", "‘얼마’ 받느냐가 핵심"],
        checklist=["통원 1일 한도", "입원 1일 한도 + 면책기간",
                   "수술비 등급별 금액", "특정 질병 진단비", "면책·감액 조항"],
        highlight_checks=2,
        bonus="*보장은 가입 조건·약관 따라 달라져요",
        cta="댓글 ‘정보’ 남기면 DM 드려요",
    )


if __name__ == "__main__":
    spec = demo_spec()
    img = render_frame(spec, t_sec=15.0)
    out = ROOT / "samples" / "phase1_static.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print(f"saved {out} ({out.stat().st_size // 1024} KB)")
