# 폰트

카드뉴스 타이틀에 쓰는 폰트 목록. 본문(subtitle/bullets/ref 등)은 가독성을 위해
항상 Pretendard 를 쓰고, **타이틀만** 디자인별로 아래 폰트 중 하나로 바뀐다
(`generate.py` 의 `FONT_CONFIGS` / `DESIGN_CONFIGS[...]["title_font"]` 참고).

| 파일 | family | 배포처 | 쓰이는 디자인 |
|---|---|---|---|
| `Pretendard-*.otf` | Pretendard | [woowahan/pretendard](https://github.com/orioncactus/pretendard) (OFL) | newsprint_* (에디토리얼 톤 유지) |
| `GmarketSansBold.otf` | Gmarket Sans | [지마켓](https://corp.gmarket.com/fonts/) | dark_copper, gradient_forest |
| `Jalnan.otf` | Jalnan (여기어때 잘난체) | [여기어때](https://www.goodchoice.kr/font) | gradient_sunset, geometric_earthy |
| `BMDOHYEON.otf` | BM DOHYEON (배민 도현체) | [우아한형제들](http://font.woowahan.com/dohyeon/) | dark_sage, geometric_navy |

각 브랜드 무료 배포 폰트라 상업적 사용에 제한이 없다 (폰트 자체를 재판매/재배포하는
게 아니라 콘텐츠에 삽입하는 용도). 배포처 페이지에서 최신 이용 약관을 다시
확인하는 걸 권장.

## 폰트 추가하는 법

1. 이 폴더에 `.otf`/`.ttf` 파일 추가
2. `generate.py` 의 `FONT_CONFIGS` 에 `{slug: {"family": "...", "file": "파일명"}}` 추가
3. `DESIGN_CONFIGS` 의 원하는 디자인 항목에 `"title_font": "slug"` 지정
