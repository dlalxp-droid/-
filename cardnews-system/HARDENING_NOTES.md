# Publish 안정성 강화 메모

이미 적용된 항목과, 다음 장애 시 즉시 적용할 후보를 따로 정리.
실제 사고 기록을 함께 남겨야 같은 함정에 두 번 안 빠진다.

## 이미 적용됨

- **`published.flag` idempotency** (`scheduler.py`)
  - 발행 성공 시 `output/<date>/<slot>/published.flag` 기록.
  - 같은 슬롯이 다시 호출되면 즉시 skip → Meta 중복 호출 방지.
- **트리거 단위 concurrency 락** (`instagram-publish.yml`)
  - `cardnews-publish-${schedule || dispatch-date-slot}` 단위로 직렬화.
  - cron catch-up 같은 동일 트리거 다중 fire 방지.
- **발행 후 flag 자동 commit/push** (`instagram-publish.yml` "Commit published.flag" 단계)
  - 성공 시 origin 에 flag 박아서 다음 트리거가 origin 체크아웃하면 보임.

## 알려진 잔여 위험 — 다음 장애 시 바로 적용

### 1) 슬롯+날짜 단위 concurrency (대기 중)

**증상**: 같은 PM 이 cron 으로 한 번 + workflow_dispatch 로 한 번 거의 동시에 깨어나면 두 런이 같이 돌아 Meta 가 code=4 로 두 번째를 차단. 이미 한 번 발생함 (2026-05-10 PM, 첫 런 IG 게시 성공 / 두 번째 런 fail).

**원인**: 현재 concurrency group 이 트리거(스케줄 cron 문자열 / dispatch 입력) 단위라 cron+dispatch 가 다른 그룹으로 분류된다.

**적용**: `instagram-publish.yml` 의 concurrency group 을 슬롯+날짜로 통일.

```yaml
# Resolve slot & date 단계로는 늦으니, group 표현식 안에서 직접 매핑.
concurrency:
  group: >-
    cardnews-publish-${{
      inputs.date ||
      (github.event.schedule == '0 0 * * *' && format('{0}-AM', github.run_id)) ||
      ...
    }}
```

워크플로 레벨 표현식에서 `Asia/Seoul` 날짜 계산이 안 되니, **default 로 KST 기준 today 를 컴포지트 액션으로 미리 계산**하거나, 단순히 `github.event.repository.updated_at` 같은 근사치 + slot 매핑으로 충분히 동시성만 막는 식으로도 OK. 핵심은 cron 과 dispatch 가 같은 슬롯이면 같은 그룹이어야 한다는 것.

### 2) Meta 호출 직전 flag 선기록 (pre-attempt 마커)

**증상**: `_post(media_publish)` 가 200 으로 IG 에 게시 성공시켰는데, 그 직후 응답 처리/네트워크 글리치로 예외가 나면 flag 가 안 써지고 다음 런이 또 같은 슬롯 발행 시도.

**적용**: `scheduler.py upload_carousel` 에서 carousel parent FINISHED 직후, `media_publish` 호출 **이전** 에 임시 flag (`publishing.flag` 같은) 를 기록. 재시도 진입 시 이 마커도 함께 검사해서 skip.

```python
# upload_carousel 안, _wait_until_finished 직후
attempt_flag = base / "publishing.flag"
attempt_flag.write_text(f"creation_id={parent['id']}\nattempt_at={...}")
# media_publish 호출
```

`scheduler.py main()` 진입부에서 `published.flag OR publishing.flag` 둘 다 체크하면 부분 성공 race 도 안전.

### 3) 인라인 fallback 의 `git checkout origin/$REF -- output` 이 로컬 flag 도 덮어씀

**증상**: 어떤 런이 로컬에 flag 를 막 만든 상태에서 동시에 다른 런이 시작되면, 두 번째 런의 fallback 단계가 origin 출력 디렉토리를 통째로 체크아웃해서 첫 런이 만든 flag 가 origin 에 push 되기 전이면 못 본다.

**적용 후보**:
- (a) fallback 의 `git checkout` 을 `--` 뒤에 더 좁은 path 로 한정 (예: 해당 슬롯의 `approved/`, `draft/`, `rejected.flag` 만).
- (b) scheduler.py 에서 flag 체크할 때 한 번 더 `git fetch + git show origin/$REF:<flag-path>` 로 origin 직접 조회.

(a) 가 단순. concurrency 단위 통일이 우선이라 (1) 적용되면 이 risk 는 줄어든다.

### 4) Meta API 응답 검증 강화

**증상**: media_publish 가 200 + 본문에 `"error"` 같은 비정상 필드를 담아도 현재는 그대로 통과 → `pub["id"]` 가 KeyError 로 raise 만 함.

**적용**: `_post` 에서 `r.json()` 결과에 `error` 키 있으면 명시적으로 raise. `pub.get("id")` 없으면 친절한 메시지로 raise.

### 5) 일일 IG posting 한도 모니터링

**적용 후보**: scheduler.py 시작에서 `/{ig_id}?fields=content_publishing_limit` 조회 → 남은 quota 확인. 0 이면 즉시 skip-and-notify.

## 사고 기록

| 날짜       | 슬롯 | 증상                                               | 원인                                                      | 대응                                       |
| ---------- | ---- | -------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------ |
| 2026-05-10 | AM   | publish 8h 늦게 실행 후 fail (issue #19)           | cron 지연 + 같은 슬롯 다중 fire                           | published.flag idempotency PR (#21)        |
| 2026-05-10 | NOON | publish 5h 늦게 실행 후 Meta code=4 (issue #20)    | AM 직후 NOON 실행 → Meta rate limit                       | 위와 동일                                  |
| 2026-05-10 | PM   | IG 게시 성공했지만 워크플로는 fail                 | 두 번째 트리거가 동시에 돌아서 Meta 가 두 번째를 차단     | published.flag 수동 커밋 (#23) — 차단 완료 |

## 운영 체크리스트 (장애 의심 시)

1. `gh run list --workflow=instagram-publish.yml --limit 10` 또는 GitHub Actions UI 에서 동일 슬롯 다중 실행 여부 확인.
2. `output/<date>/<slot>/published.flag` 가 origin 에 박혔는지 확인. 박혔다면 IG 게시는 끝난 것 — 다음 cron 이 자동 skip 한다.
3. IG 에 실제 게시됐는데 flag 가 없다면 즉시 수동 flag 커밋 (default branch 에 push). 안 그러면 다음 cron 이 또 올린다.
4. Meta error code=4 / subcode 2207051 = "Application request limit reached". 24h 한도 초과거나 짧은 시간 중복. 한도면 다음 날까지 대기.
