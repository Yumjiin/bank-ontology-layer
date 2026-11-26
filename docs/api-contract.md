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
| get_linked_objects | POST `/objects/{type}/{id}/links/{link}` | `fixtures/linked_objects.json` |
| get_metric | POST `/metrics/{name}` | `fixtures/metric.json` |
| request_action (허용) | POST `/actions/{action_type}` | `fixtures/action_pending.json` |
| request_action (거부) | POST `/actions/{action_type}` | `fixtures/action_forbidden.json` (HTTP 403) |
| — | GET `/approvals?status=PENDING` | `fixtures/approvals.json` |
| — | POST `/approvals/{id}/approve` | `fixtures/approve.json` |
| — | POST `/traces` | 실행 단계 저장, HTTP 201과 `trace_id`, `step` 반환 |
| — | GET `/traces?session_id=` | `fixtures/traces.json` |

### 에이전트 실행 기록

에이전트는 질문마다 하나의 `trace_id`를 만들고, 실행 시작과 각 모델·도구 호출을 `agent_trace`에 순서대로 저장함. 첫 온톨로지 조회와 실패한 호출도 기록함.

`POST /traces`는 `trace_id`, `step`(1부터 시작), `span_type`(`invoke_agent`, `chat`, `execute_tool`), `output_summary`를 받음. 단계에 따라 `tool_name`, `input`, `sql_text`, `policy_decision`, `latency_ms`를 함께 보낼 수 있음. 세션은 `X-Session-Id` 헤더로 지정함.

`GET /traces`는 쿼리의 `session_id`와 `X-Session-Id`가 일치하는 세션의 기록을 저장 순서로 반환함. 각 항목의 `trace_id`로 같은 질문의 단계를 묶을 수 있음.

### 개수 질문의 `total`

`search_objects`와 `get_linked_objects`는 `limit`과 관계없이 조건에 맞는 전체 개수 `total`을 반환함. `count`는 응답에 담긴 항목 수이며, `total > count`이면 `has_more`가 true임.

`몇 명/몇 건` 질문은 `total`로 답함. `get_metric`은 활성 고객·순입금액처럼 업무 정의가 필요한 지표에 사용함. 지표 필터에는 `/ontology`의 해당 지표에 있는 `filterable_objects`에 나열된 객체의 속성을 사용함.

### 오류 응답

400·403·404·503은 모두 `{"detail": {"code": ..., "message": ...}}` 형식임 (`fixtures/error_invalid_request.json`).

| 상태 코드 | code | 의미 | 에이전트 처리 |
| --- | --- | --- | --- |
| 400 | `INVALID_REQUEST` | 존재하지 않는 객체·속성·링크·지표, 정의에 없는 값, 잘못된 날짜 형식 | `message`(허용값 포함)를 LLM에 전달해 다시 시도 |
| 403 | `FORBIDDEN_ROLE` | 역할 권한 없음 | `message`를 그대로 사용자에게 설명하고 다른 속성으로 우회 조회하지 않음 |
| 404 | `NOT_FOUND` | ID에 해당하는 객체 없음 | 사용자에게 없음을 알림 |
| 422 | — | 요청 형식 오류 (헤더 누락 등) | 코드 버그, 수정 필요 |
| 501 | — | 아직 구현되지 않음 | 개발 중에만 발생 |
| 503 | `DB_UNAVAILABLE` | DB 연결 실패 | 잠시 후 재시도 |
