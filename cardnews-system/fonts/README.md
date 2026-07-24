# 폰트

카드뉴스 타이틀에 쓰는 폰트 목록. 본문(subtitle/bullets/ref 등)은 가독성을 위해
항상 Pretendard 를 쓰고, **타이틀만** 트렌디 폰트로 바뀐다.

newsprint_* (에디토리얼 톤) 디자인은 항상 Pretendard 유지 — 트렌디 폰트가 안
어울려서. 나머지 디자인(dark/gradient/geometric)은 아래 트렌디 폰트 풀
(`TRENDY_FONT_SLUGS`, `generate.py`)에서 슬롯 순서(idx)대로 순환 배정된다 —
같은 디자인이라도 매번 다른 폰트가 "돌려가며" 쓰인다 (`_title_font_for` 참고).

| 파일 | family | 배포처 |
|---|---|---|
| `Pretendard-*.otf` | Pretendard | [orioncactus/pretendard](https://github.com/orioncactus/pretendard) (OFL) |
| `GmarketSansBold.otf` | Gmarket Sans | [지마켓](https://corp.gmarket.com/fonts/) |
| `Jalnan.otf` | Jalnan (여기어때 잘난체) | [여기어때](https://www.goodchoice.kr/font) |
| `BMDOHYEON.otf` | BM DOHYEON (배민 도현체) | [우아한형제들](https://www.woowahan.com/fonts) |
| `BMHANNAPro.otf` | BM HANNA Pro (배민 한나체 Pro) | [우아한형제들](https://www.woowahan.com/fonts) |
| `BMEULJIRO.otf` | BM EULJIRO (배민 을지로체) | [우아한형제들](https://www.woowahan.com/fonts) |
| `BMKIRANGHAERANG.otf` | BM KIRANGHAERANG (배민 기랑해랑체) | [우아한형제들](https://www.woowahan.com/fonts) |
| `CookieRun-Bold.otf` | CookieRun (쿠키런체) | [쿠키런 폰트](https://www.cookierunfont.com/) |
| `TmonMonsori.ttf` | Tmon Monsori (티몬 몬소리체) | [티몬](https://brunch.co.kr/@creative/32) |
| `NEXONLv1Gothic-Bold.otf` | NEXON Lv1 Gothic (넥슨 Lv.1 고딕) | [넥슨](http://levelup.nexon.com/font/index.aspx?page=1) |
| `Cafe24Dangdanghae.otf` | Cafe24 Dangdanghae (카페24 당당해) | [카페24](https://fonts.cafe24.com/) |
| `OwnglyphDaGyeong.otf` | Ownglyph_2022_UWY_Da_Gyeong (온글잎 다경체) | [온글잎](https://www.ownglyph.com/) |

각 브랜드 무료 배포 폰트라 상업적 사용에 제한이 없다 (폰트 자체를 재판매/재배포하는
게 아니라 콘텐츠에 삽입하는 용도). 배포처 페이지에서 최신 이용 약관을 다시
확인하는 걸 권장.

## 폰트 추가하는 법

1. 이 폴더에 `.otf`/`.ttf` 파일 추가
2. `generate.py` 의 `FONT_CONFIGS` 에 `{slug: {"family": "...", "file": "파일명"}}` 추가
   (`TRENDY_FONT_SLUGS` 는 `FONT_CONFIGS` 에서 자동으로 파생되므로 순환 풀에도
   자동 편입된다)

## ⚠️ 렌더링 시 주의 — `page.set_content()` 로 폰트 로드 안 됨

Playwright 로 카드를 렌더링할 때 `page.set_content(html)` 을 쓰면 문서 origin 이
`about:blank` 취급돼서 Chromium 이 `file://` 로컬 폰트 로드를 전부 막는다
("Not allowed to load local resource"). 그런데 실패해도 시스템 기본 산세리프로
조용히 폴백되기 때문에 화면상 "그럴싸하게" 보여서 눈치채기 매우 어렵다 (실제로
이 폰트 시스템 초기 구현이 몇 시간 동안 이 문제를 안고 있었는데 육안으로는
구분이 안 됐음 — `document.fonts` 로 status 를 직접 찍어봐야 드러남).

`render_set()` 은 이 문제를 피하려고 카드마다 임시 `.html` 파일에 써서
`page.goto(file://...)` 로 실제 navigate 한다 (같은 file:// origin 이라 로컬
폰트 로드가 허용됨). 렌더링 코드를 고칠 때 `page.set_content()` 로 되돌리지
말 것 — 되돌리면 폰트가 전부 조용히 깨진다.
