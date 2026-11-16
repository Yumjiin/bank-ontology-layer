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

---

## 엔드포인트와 응답 예시

| 도구 | 메서드·경로 | 응답 예시 |
| --- | --- | --- |
| describe_ontology | GET `/ontology` | 실제 동작함 (fixture 불필요) |
| search_objects | POST `/objects/search` | `fixtures/objects_search.json` |
| get_linked_objects | POST `/objects/{type}/{id}/links/{link}` | `fixtures/linked_objects.json` |
| get_metric | POST `/metrics/{name}` | `fixtures/metric.json` |
| request_action (허용) | POST `/actions/{action_type}` | `fixtures/action_pending.json` |
| request_action (거부) | POST `/actions/{action_type}` | `fixtures/action_forbidden.json` (HTTP 403) |
| — | GET `/approvals?status=PENDING` | `fixtures/approvals.json` |
| — | POST `/approvals/{id}/approve` | `fixtures/approve.json` |
| — | GET `/traces?session_id=` | `fixtures/traces.json` |

### 오류 응답

| 상태 코드 | 의미 | 에이전트 처리 |
| --- | --- | --- |
| 400 | 존재하지 않는 객체·속성·링크·지표, 잘못된 필터 값 | `detail`을 LLM에 전달해 다시 시도 |
| 403 | 역할 권한 없음 | `detail`의 사유를 사용자에게 설명 |
| 422 | 요청 형식 오류 (헤더 누락 등) | 코드 버그, 수정 필요 |
| 501 | 아직 구현되지 않음 | 개발 중에만 발생 |
