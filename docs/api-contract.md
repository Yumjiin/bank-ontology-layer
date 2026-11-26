# API 계약 (Ontology Service ↔ Agent)

서비스(지인)와 에이전트(수민)가 함께 지키는 약속임. 서비스 구현 전에도 에이전트와 화면은 `docs/fixtures/`의 예시 응답으로 개발할 수 있음. 형식을 바꿀 때는 이 문서와 fixtures를 같은 PR에서 함께 수정함.

---

## 공통

| 항목 | 내용 |
| --- | --- |
| Base URL | 컨테이너 안: `http://service:8000` / 로컬: `http://localhost:8001` |
| 필수 헤더 | `X-Role`: `teller` / `marketer` / `manager`, `X-Session-Id`: 화면 세션 ID |
| 요청 형식 | `service/schemas.py`의 Pydantic 모델 (Swagger `/docs`에서 확인 가능) |
| 정책 결과 | 모든 조회 응답에 `policy` 필드 포함: `{"masked": [...], "blocked": [...]}` |
| 추적 | 모든 응답에 `trace_id` 포함 |
| SQL | 조회 응답에 서비스가 실행한 `sql` 포함 (실행 기록 패널·시연용, 값이 채워진 표시용 문자열) |

### 필터 형식

값은 사람이 쓰는 표현으로 보냄. 원천 코드로의 변환은 서비스가 담당함.

```json
{"property": "grade", "op": "eq", "value": "VIP"}
{"property": "type", "op": "in", "value": ["입금", "출금"]}
{"property": "date", "op": "between", "value": ["2026-07-01", "2026-09-30"]}
```

| op | 의미 |
| --- | --- |
| eq | 같음 |
| in | 목록 중 하나 |
| gte / lte | 이상 / 이하 |
| between | 범위 (양 끝 포함) |

- 코드값 속성은 `/ontology`의 `values`에 있는 값만 허용함 (원천 코드 `'IN'` 등을 직접 보내면 400)
- 날짜는 `YYYY-MM-DD`만 허용함

---

## 엔드포인트와 응답 예시

| 도구 | 메서드·경로 | 응답 예시 |
| --- | --- | --- |
| describe_ontology | GET `/ontology` | 실제 동작함 (fixture 불필요) |
| search_objects | POST `/objects/search` | `fixtures/objects_search.json` (**동작함**) |
| — | GET `/objects/{type}/{id}` | `fixtures/object.json` (**동작함**) |
| get_linked_objects | POST `/objects/{type}/{id}/links/{link}` | `fixtures/linked_objects.json` (**동작함**) |
| get_metric | POST `/metrics/{name}` | `fixtures/metric.json` (**동작함**) |
| request_action (허용) | POST `/actions/{action_type}` | `fixtures/action_pending.json` |
| request_action (거부) | POST `/actions/{action_type}` | `fixtures/action_forbidden.json` (HTTP 403) |
| — | GET `/approvals?status=PENDING` | `fixtures/approvals.json` |
| — | POST `/approvals/{id}/approve` | `fixtures/approve.json` |
| — | GET `/traces?session_id=` | `fixtures/traces.json` |

### 역할별 정책 (`X-Role`)

`ontology.yaml`의 속성 `sensitivity`(예: PII)와 `roles` 정의에 따라 모든 조회 응답에 적용됨.

| 역할 | PII 속성 (고객명) |
| --- | --- |
| teller (상담원) | 원문 |
| marketer (마케팅) | `***`로 마스킹 |
| manager (관리자) | 원문 |

- 응답의 `policy.masked` / `policy.blocked`에 이번 응답에서 마스킹·차단된 속성이 기록됨 (`fixtures/objects_search_marketer.json`)
- 마스킹·차단된 속성은 **필터로도 쓸 수 없음** → 403 `FORBIDDEN_ROLE` (`fixtures/error_forbidden_filter.json`). 값을 못 보는 역할이 이름으로 검색하면 결과 유무로 정보를 알아낼 수 있기 때문
- `total`, 지표 값은 개인을 식별하지 않으므로 역할과 무관하게 같음
- 에이전트는 403 `message`를 그대로 사용자에게 설명함 (다른 속성으로 우회 검색하지 않음)

### 개수 질문: `total` 사용

`search_objects`와 `get_linked_objects` 응답에는 `limit`과 관계없이 조건에 맞는 전체 개수 `total`이 들어 있음.

| 필드 | 의미 |
| --- | --- |
| `count` | 이번 응답에 담긴 항목 수 (최대 `limit`) |
| `total` | 조건에 맞는 전체 개수 |
| `has_more` | `total > count`이면 true (항목 목록이 잘렸다는 뜻) |

에이전트 규칙
- "몇 명/몇 건" 질문은 `total`로 답함. 결과를 전부 받아서 셀 필요 없으므로 `limit`은 작게 둬도 됨 (예: 여성 고객 수 → `total: 173`)
- `has_more`가 true면 `items`를 전체 결과로 간주하지 않음
- `get_metric`은 활성 고객·순입금액처럼 **회사 정의가 필요한 지표**에만 사용함

### 링크 조회

- `object_type`, `object_id`는 출발 객체, `filters`·`properties`는 도착 객체 기준
- 링크는 정의된 방향으로만 따라감 (예: `owns`는 Customer → Account). 반대 방향 요청은 400과 함께 출발 객체에서 쓸 수 있는 링크 목록을 돌려줌
- 도착 객체의 기본 필터가 적용됨 (예: `has_transaction`은 취소 거래 제외)
- 출발 객체가 없으면 404

### 지표 계산

- 계산 규칙은 `ontology.yaml`의 `metrics` 정의를 따름 (명의인만, 이자·수수료 제외, 취소 거래 제외 등)
- `filters`에는 `/ontology` 응답의 `filterable_objects`에 있는 객체의 속성만 쓸 수 있음 (예: `net_deposit`에 `{"property": "channel", "op": "eq", "value": "모바일뱅킹"}`)
- 같은 이름의 속성이 경로상 여러 객체에 있으면 `"Account.acct_no"`처럼 객체를 붙여야 함
- 지표 정의에 고정된 조건(예: 거래 유형 입금·출금)은 요청 필터로 바꿀 수 없음 (400)

### 오류 응답

400·403·404·503은 모두 `{"detail": {"code": ..., "message": ...}}` 형식임 (`fixtures/error_invalid_request.json`).

| 상태 코드 | code | 의미 | 에이전트 처리 |
| --- | --- | --- | --- |
| 400 | `INVALID_REQUEST` | 존재하지 않는 객체·속성·링크·지표, 정의에 없는 값, 잘못된 날짜 형식 | `message`(허용값 포함)를 LLM에 전달해 다시 시도 |
| 403 | `FORBIDDEN_ROLE` | 역할 권한 없음 (마스킹 속성 필터, 허용되지 않은 액션) | `message`의 사유를 사용자에게 설명 |
| 404 | `NOT_FOUND` | ID에 해당하는 객체 없음 | 사용자에게 없음을 알림 |
| 422 | — | 요청 형식 오류 (헤더 누락 등) | 코드 버그, 수정 필요 |
| 501 | — | 아직 구현되지 않음 | 개발 중에만 발생 |
| 503 | `DB_UNAVAILABLE` | DB 연결 실패 | 잠시 후 재시도 |
