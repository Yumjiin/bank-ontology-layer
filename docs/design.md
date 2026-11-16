# Bank Ontology Layer 설계서

AI 에이전트를 위한 은행 데이터 온톨로지 레이어 구축 — BOAZ DE 미니 프로젝트

| 항목 | 내용 |
| --- | --- |
| 기간 | 2026.11.17 ~ 2026.12.01 (12/2 발표·시연, 12/5 공동세션 발표) |
| 인원 | 3명 |
| 저장소 | `bank-ontology-layer` (GitHub) |
| 실행 | `docker compose up --build` 한 줄로 전체 실행 |

---

## 1. 프로젝트 개요

### 1-1. 배경

인사이트 발표("AI가 파이프라인을 짜는 시대, 데이터 엔지니어는 의미를 설계한다")에서 확인한 내용은 아래와 같음.

| 발표 내용 | 확인한 사실 |
| --- | --- |
| Text-to-SQL 실험 (데모 A) | 테이블 구조만 주면 LLM이 코드값·날짜 형식·명의인 구분을 틀려 오답을 냄 (활성 고객 0명, 순입금액 0원) |
| 정의 문서 제공 (데모 B) | 정의 문서를 프롬프트에 붙이면 2문항 모두 정답을 냄 |
| 검증 루프 | 형식 오류는 고치지만 정의 오류(대리인 포함 278명)는 검증을 통과한 채 오답으로 남음 |
| Foundry 구조 | 객체·관계·액션·권한을 온톨로지 계층에 정의하고, 조회(OSS)와 수정(Actions)이 모두 이 계층을 거치게 해서 거버넌스를 구현함 |

발표 데모는 정의를 **프롬프트로** 전달했기 때문에, LLM이 정의를 읽고도 SQL을 잘못 쓸 가능성이 남아 있었음. 또한 2문항 1회 실행이라 규모와 재현성에 한계가 있었음.

### 1-2. 목적

> 의미와 권한을 프롬프트가 아니라 **시스템 계층**으로 구현함. LLM 에이전트는 DB에 직접 접근하지 않고 온톨로지 레이어가 제공하는 도구로만 데이터를 조회·수정함.

| 목표 | 내용 |
| --- | --- |
| 정확성 | 비즈니스 용어·코드값·조인 경로를 온톨로지에 한 번 정의하고, SQL은 레이어가 정의대로 생성함 |
| 거버넌스 | 역할별로 민감 속성을 마스킹·차단하고, 수정 행동은 허용된 역할만 승인 절차를 거쳐 실행함 |
| 추적성 | 에이전트의 도구 호출, 생성 SQL, 권한 판정을 실행 기록으로 남김 |
| 재현성 | `docker compose up` 한 줄로 DB·서비스·데모가 모두 실행됨 |

### 1-3. 범위

| 구분 | 포함 내용 |
| --- | --- |
| MVP (필수) | 객체 4종, 링크 3종, 지표 2~3종, 역할 3종, 액션 1종, 정책 계층, 에이전트 도구, Streamlit 데모, Docker 실행 |
| 확장 (여유 시) | 실행 기록 패널, 질문셋 정확도 평가, MCP 서버 노출, Neo4j 관계 시각화 |
| 범위 밖 | 실제 은행 데이터, 사용자 인증(로그인), 운영 배포, 행 단위 권한(Row-level security) |

---

## 2. 시스템 아키텍처

### 2-1. 전체 구조

```
┌──────────────────────────────────────────────┐
│  app (Streamlit)                              │
│  채팅 화면 / 역할 선택 / 실행 기록 패널        │
└───────────────────────┬──────────────────────┘
                        │ 사용자 질문 + 역할
                        ▼
┌──────────────────────────────────────────────┐
│  agent (LLM + function calling)               │
│  SQL을 직접 쓰지 않고 도구만 호출함            │
└───────────────────────┬──────────────────────┘
                        │ HTTP (X-Role 헤더)
                        ▼
┌──────────────────────────────────────────────┐
│  service (FastAPI) — Ontology Service         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Metadata │ │  Query   │ │ Actions  │       │
│  │  (OMS)   │ │  (OSS)   │ │          │       │
│  └──────────┘ └──────────┘ └──────────┘       │
│  ┌──────────┐ ┌──────────┐                    │
│  │  Policy  │ │  Trace   │                    │
│  └──────────┘ └──────────┘                    │
└───────────────────────┬──────────────────────┘
                        │ 파라미터 바인딩된 SQL
                        ▼
┌──────────────────────────────────────────────┐
│  db (PostgreSQL)                              │
│  은행 목데이터 + 승인 요청 + 실행 기록 테이블  │
└──────────────────────────────────────────────┘
```

### 2-2. Foundry 구성요소와의 대응

발표 2(Foundry)에서 다룬 구성요소를 축소해 구현함. 대응 관계는 팀 해석임.

| Foundry | 본 프로젝트 | 역할 |
| --- | --- | --- |
| Ontology Metadata Service (OMS) | `service/metadata.py` + `ontology.yaml` | 객체·속성·관계·지표·액션·권한 정의를 로드하고 제공함 |
| Object Set Service (OSS) | `service/query.py` | 객체 조회 요청을 정의에 맞는 SQL로 변환해 실행함 |
| Actions | `service/actions.py` | 권한 검증 후 승인 요청 생성, 승인 시 수정 실행 |
| Markings / Object-level Permission | `service/policy.py` | 역할별 속성 마스킹·차단, 액션 허용 여부 판정 |
| AIP | `agent/` | LLM이 온톨로지 도구로만 데이터에 접근함 |
| Workshop | `app/` | 사용자와 상호작용하는 화면 |

### 2-3. 설계 원칙

| 원칙 | 내용 |
| --- | --- |
| 정의는 한 곳에만 | 코드값 해석, 날짜 형식, 명의인 조건, 지표 계산식은 `ontology.yaml`에만 존재함 |
| LLM은 SQL을 쓰지 않음 | 에이전트는 객체 타입·필터·링크·지표 이름만 고름. SQL 생성은 서비스가 담당함 |
| 모든 접근은 정책을 거침 | 조회 결과와 액션 요청은 반드시 Policy 계층을 통과함 |
| 사람 승인 | 데이터를 바꾸는 액션은 즉시 실행하지 않고 승인 요청으로 생성함 (Human-in-the-loop) |
| 모든 호출은 기록됨 | 도구 호출 1회마다 실행 기록 1행을 남김 |

---

## 3. 데이터 모델

### 3-1. 원천 테이블

발표 데모의 목데이터(SQLite)를 PostgreSQL로 이관함. 생성 스크립트는 SEED 고정이므로 같은 데이터가 재생성됨. 컬럼 목록은 기존 `generate_mock_data.py` 기준으로 확정해야 함.

| 테이블 | 행 수 | 설명 | 의도한 함정 |
| --- | --- | --- | --- |
| brnc | 8 | 지점 | 컬럼명 attr1~attr5로 의미를 알 수 없음 |
| cust | 300 | 고객 | — |
| acct | 317 | 계좌 | 계좌상태코드 'A', 'D', 'C' |
| acct_rel | 362 | 계좌관계 | 명의인 '01'과 대리인 '02'가 섞여 있음 |
| txn | 12,908 | 2026년 1~9월 거래 | 거래코드 IN/OUT/INT/FEE, 'YYYYMMDD' 문자열 날짜, 부호 없는 금액, 취소 여부 cncl_yn |

### 3-2. 추가 테이블

| 테이블 | 용도 | 주요 컬럼 |
| --- | --- | --- |
| action_request | 액션 승인 요청 | request_id, action_type, target_id, params(JSONB), requested_role, status(PENDING/APPROVED/REJECTED/EXECUTED), created_at, decided_at |
| agent_trace | 실행 기록 | trace_id, session_id, step, span_type, tool_name, input(JSONB), output_summary, sql_text, policy_decision, latency_ms, created_at |

`span_type`은 발표 3(Agent Observability)의 Span 구분을 따라 `invoke_agent`, `chat`, `execute_tool` 중 하나로 기록함.

---

## 4. 온톨로지 정의 (ontology.yaml)

### 4-1. 구성 요소

| 구성 요소 | 의미 | 본 프로젝트 정의 대상 |
| --- | --- | --- |
| object_types | 현실의 대상과 원천 테이블 매핑 | Customer, Account, Transaction, Branch |
| properties | 객체의 속성, 컬럼 매핑, 코드값 해석, 민감도 | 고객명(PII), 계좌상태, 거래유형, 거래일자 등 |
| link_types | 객체 간 관계와 조인 조건 | owns(명의인), delegated(대리인), has_transaction, managed_by |
| metrics | 비즈니스 지표와 계산 규칙 | active_customers, net_deposit, (선택) dormant_accounts |
| action_types | 객체를 바꾸는 행동과 허용 역할 | freeze_account |
| roles | 역할별 접근 정책 | teller, marketer, manager |

### 4-2. 정의 예시

아래는 형식 예시임. 컬럼명과 지표 정의는 기존 `semantic_definitions.yaml`의 내용을 그대로 이관해 확정함.

```yaml
version: 0.1

object_types:
  Customer:
    table: cust
    primary_key: cust_no
    description: 은행과 거래 관계가 있는 개인 고객
    properties:
      cust_no:   { column: cust_no }
      name:      { column: cust_nm, sensitivity: PII }
      grade:
        column: cust_grd_cd
        values: { "01": VIP, "02": 일반 }

  Account:
    table: acct
    primary_key: acct_no
    properties:
      status:
        column: acct_stat_cd
        values: { A: 활성, D: 휴면, C: 해지 }

  Transaction:
    table: txn
    primary_key: txn_no
    properties:
      date:
        column: txn_dt
        type: date
        format: YYYYMMDD          # 문자열 날짜 변환 규칙을 여기서만 정의함
      type:
        column: txn_cd
        values: { IN: 입금, OUT: 출금, INT: 이자, FEE: 수수료 }
      amount:    { column: amt, type: number }
      cancelled: { column: cncl_yn, values: { Y: true, N: false } }
    default_filters:
      - cancelled = false         # 취소 거래는 기본 제외

link_types:
  owns:
    description: 고객이 명의인으로 보유한 계좌
    from: Customer
    to: Account
    via:
      table: acct_rel
      from_key: cust_no
      to_key: acct_no
      condition: "rel_cd = '01'"
  delegated:
    description: 고객이 대리인으로 등록된 계좌
    from: Customer
    to: Account
    via:
      table: acct_rel
      from_key: cust_no
      to_key: acct_no
      condition: "rel_cd = '02'"
  has_transaction:
    from: Account
    to: Transaction
    join: { from_key: acct_no, to_key: acct_no }

metrics:
  active_customers:
    description: 기간 내 명의인 계좌에서 고객 거래가 1건 이상 발생한 고객 수
    object: Customer
    path: [owns, has_transaction]
    filters: [ "Transaction.type in [IN, OUT]" ]
    aggregate: count_distinct(Customer.cust_no)
    params: [ period ]
  net_deposit:
    description: 기간 내 입금액 합계에서 출금액 합계를 뺀 값 (이자·수수료 제외)
    object: Transaction
    expression: "sum(case type=IN then amount when type=OUT then -amount)"
    params: [ period ]

action_types:
  freeze_account:
    description: 계좌를 지급정지 상태로 변경함
    target: Account
    params: { reason: string }
    allowed_roles: [ manager ]
    requires_approval: true

roles:
  teller:   { mask: [], block: [] }
  marketer: { mask: [ PII ], block: [] }
  manager:  { mask: [], block: [] }
```

### 4-3. 정의 검증

서비스 기동 시 `ontology.yaml`을 검증하고, 오류가 있으면 기동을 중단함.

| 검증 항목 | 내용 |
| --- | --- |
| 스키마 | Pydantic 모델로 필수 키·타입 확인 |
| 참조 무결성 | link·metric·action이 참조하는 객체 타입과 속성이 존재하는지 확인 |
| DB 정합성 | 매핑된 테이블·컬럼이 실제 DB에 존재하는지 information_schema로 확인 |
| 코드값 | values에 정의된 코드가 실제 데이터의 DISTINCT 값을 모두 포함하는지 확인 (경고) |

---

## 5. Ontology Service (FastAPI)

### 5-1. 모듈 구성

| 모듈 | 역할 |
| --- | --- |
| `metadata.py` | `ontology.yaml` 로드·검증, 메타데이터 조회 API 제공 |
| `compiler.py` | 객체 조회·링크 탐색·지표 요청을 SQL로 변환함 (SQLAlchemy Core, 파라미터 바인딩) |
| `query.py` | 조회 API, compiler 호출 후 결과를 코드값 해석·정책 적용해 반환 |
| `policy.py` | 역할별 속성 마스킹·차단, 액션 허용 판정 |
| `actions.py` | 승인 요청 생성, 승인·거절, 승인 시 실행 |
| `trace.py` | 실행 기록 저장·조회 |
| `main.py` | 라우터 등록, 기동 시 정의 검증 |

### 5-2. 필터 표현

LLM이 임의 SQL을 넣지 못하도록 필터는 제한된 구조로만 받음.

| 연산자 | 예시 |
| --- | --- |
| eq | `{"property": "grade", "op": "eq", "value": "VIP"}` |
| in | `{"property": "type", "op": "in", "value": ["입금", "출금"]}` |
| gte / lte | `{"property": "date", "op": "gte", "value": "2026-07-01"}` |
| between | `{"property": "date", "op": "between", "value": ["2026-07-01", "2026-09-30"]}` |

값은 사람이 쓰는 표현(VIP, 입금, 2026-07-01)으로 받고, compiler가 정의를 보고 원천 표현('01', 'IN', '20260701')으로 변환함. 발표 데모 A의 날짜 형식 오류와 존재하지 않는 코드값 생성이 이 단계에서 구조적으로 차단됨.

### 5-3. API 명세

모든 요청은 `X-Role` 헤더(teller / marketer / manager)와 `X-Session-Id` 헤더를 포함함.

| 메서드 | 경로 | 설명 |
| --- | --- | --- |
| GET | `/health` | 상태 확인 |
| GET | `/ontology` | 객체 타입·링크·지표·액션 목록과 설명 (에이전트 시스템 프롬프트 구성에 사용) |
| POST | `/objects/search` | 객체 검색: object_type, filters, properties, limit |
| GET | `/objects/{type}/{id}` | 객체 단건 조회 |
| POST | `/objects/{type}/{id}/links/{link}` | 링크를 따라 연결된 객체 조회 (filters 선택) |
| POST | `/metrics/{name}` | 지표 계산: period, filters |
| POST | `/actions/{action_type}` | 액션 요청 → 권한 확인 후 승인 요청 생성 |
| GET | `/approvals?status=PENDING` | 승인 대기 목록 |
| POST | `/approvals/{id}/approve` | 승인 후 실행 (manager만) |
| POST | `/approvals/{id}/reject` | 거절 (manager만) |
| GET | `/traces?session_id=` | 세션별 실행 기록 |

### 5-4. 응답 예시

`POST /objects/search` (X-Role: marketer)

```json
{
  "object_type": "Customer",
  "count": 2,
  "items": [
    { "cust_no": "C0012", "name": "***", "grade": "VIP" },
    { "cust_no": "C0047", "name": "***", "grade": "VIP" }
  ],
  "policy": { "masked": ["name"], "blocked": [] },
  "trace_id": "t-20261202-0001"
}
```

---

## 6. 정책 (거버넌스)

### 6-1. 역할별 권한

| 역할 | 조회 | PII 속성 | freeze_account |
| --- | --- | --- | --- |
| teller (상담원) | 가능 | 원문 노출 | 요청 불가 |
| marketer (마케팅) | 가능 | 마스킹 (`***`) | 요청 불가 |
| manager (관리자) | 가능 | 원문 노출 | 요청 가능, 승인 후 실행 |

### 6-2. 처리 순서

```
요청 수신
  → 역할 확인
  → (조회) compiler가 SQL 생성·실행 → 결과에 마스킹·차단 적용 → 반환
  → (액션) allowed_roles 확인 → 불허 시 403 + 사유 기록
                              → 허용 시 action_request(PENDING) 생성
                              → 승인 시 실행 → EXECUTED
  → 모든 단계 agent_trace에 기록
```

정책 위반은 에러로 숨기지 않고 사유를 에이전트에 반환함. 에이전트는 "해당 역할로는 계좌 정지를 요청할 수 없음"처럼 사용자에게 설명함.

---

## 7. Agent

### 7-1. 도구 정의

LLM function calling으로 아래 도구만 제공함. 각 도구는 Ontology Service API를 호출함.

| 도구 | 호출 API | 용도 |
| --- | --- | --- |
| `describe_ontology` | GET `/ontology` | 사용 가능한 객체·링크·지표 확인 |
| `search_objects` | POST `/objects/search` | 조건에 맞는 객체 검색 |
| `get_linked_objects` | POST `/objects/{type}/{id}/links/{link}` | 관계를 따라 탐색 |
| `get_metric` | POST `/metrics/{name}` | 정의된 지표 계산 |
| `request_action` | POST `/actions/{action_type}` | 데이터 변경 요청 (승인 대기) |

### 7-2. 에이전트 루프

| 단계 | 내용 |
| --- | --- |
| 1 | 시스템 프롬프트에 온톨로지 요약(`/ontology` 결과)과 규칙("지표 질문은 get_metric을 우선 사용", "SQL을 작성하지 말 것")을 넣음 |
| 2 | 사용자 질문 → LLM이 도구 호출 결정 |
| 3 | 도구 결과(정책 판정 포함)를 LLM에 다시 전달 |
| 4 | 최대 반복 횟수(예: 6회) 안에서 반복 후 최종 답변 |
| 5 | 각 LLM 호출과 도구 호출을 agent_trace에 기록 |

LLM 제공자는 환경변수로 선택함(`LLM_PROVIDER`, `LLM_MODEL`). 팀 내 사용 가능한 API 키에 맞춰 확정함.

---

## 8. 데모 (Streamlit)

### 8-1. 화면 구성

| 영역 | 내용 |
| --- | --- |
| 사이드바 | 역할 선택 (teller / marketer / manager), 세션 초기화, 승인 대기 목록 (manager일 때) |
| 본문 | 채팅 화면 |
| 하단 패널 | 실행 기록: 단계별 도구 이름, 입력, 생성 SQL, 정책 판정, 소요 시간 |

### 8-2. 시연 시나리오

| 순서 | 역할 | 질문 | 보여줄 포인트 |
| --- | --- | --- | --- |
| 1 | teller | 2026년 3분기 활성 고객 수는? | 발표 데모 A에서 틀린 질문을 온톨로지 경유로 정답(165명) 처리함 |
| 2 | teller | 2026년 9월 순입금액은? | 이자·수수료 제외, 취소 거래 제외가 정의대로 적용됨 (정답 389,638,000원) |
| 3 | teller | VIP 고객 중 3분기 거래가 없는 고객의 계좌는? | 객체와 링크를 따라가는 탐색 |
| 4 | marketer | 3번과 같은 질문 | 고객명이 마스킹되어 반환됨 |
| 5 | teller | 이 계좌 지급정지해줘 | 권한 없음으로 거부, 사유 설명 |
| 6 | manager | 이 계좌 지급정지해줘 | 승인 요청 생성 → 사이드바에서 승인 → 실행 |
| 7 | — | 실행 기록 패널 확인 | 각 단계의 도구 호출과 SQL 추적 |

시연 질문은 리허설에서 반복 실행해 결과가 안정적인 것만 사용함. 라이브 실패에 대비해 시연 녹화 영상을 백업으로 준비함. 12/5 공동세션에서는 시연 장면 캡처로 대체함.

---

## 9. 평가 (확장 범위)

| 항목 | 내용 |
| --- | --- |
| 질문셋 | 20~30문항, 각 문항에 정답 값 기록 (`eval/questions.yaml`) |
| 비교 조건 | (A) 스키마만 + LLM이 SQL 작성 / (B) 정의 문서 + LLM이 SQL 작성 / (C) 온톨로지 레이어 경유 |
| 반복 | 조건별 3회 실행 |
| 지표 | 정답률(실행 결과 일치), 평균 도구 호출 수, 평균 소요 시간 |
| 실행 | `docker compose run --rm eval` |

(A)와 (B)는 발표 데모 방식을 자동화한 것으로, 발표 결과를 규모 있게 재검증하는 의미가 있음.

---

## 10. 저장소 구조

```
bank-ontology-layer/
├── docker-compose.yml
├── .env.example
├── README.md
├── ontology/
│   └── ontology.yaml
├── data/
│   ├── Dockerfile
│   ├── generate_mock_data.py      # 발표 데모 스크립트를 PostgreSQL용으로 수정
│   └── schema.sql                 # 원천 테이블 + action_request + agent_trace DDL
├── service/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── metadata.py
│   ├── compiler.py
│   ├── query.py
│   ├── policy.py
│   ├── actions.py
│   ├── trace.py
│   └── tests/
│       ├── test_compiler.py
│       └── test_policy.py
├── agent/
│   ├── tools.py
│   └── loop.py
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── streamlit_app.py          # agent 모듈을 import해 사용
├── eval/
│   ├── questions.yaml
│   └── run_eval.py
└── docs/
    ├── architecture.png
    └── demo/                      # 시연 캡처
```

---

## 11. Docker 실행 구성

### 11-1. 서비스 구성

| 서비스 | 이미지 | 역할 | 실행 순서 |
| --- | --- | --- | --- |
| db | postgres:16 | 데이터 저장 | 1 (healthcheck 통과 대기) |
| seed | `data/Dockerfile` | 스키마 생성 + 목데이터 적재 후 종료 | 2 (db healthy 이후) |
| service | `service/Dockerfile` | Ontology Service (포트 8001) | 3 (seed 완료 이후) |
| app | `app/Dockerfile` | Streamlit 데모 (포트 8502) | 4 (service 이후) |
| eval | `service/Dockerfile` 재사용 | 평가 실행 (profile로 분리, 기본 실행 제외) | 수동 |

### 11-2. docker-compose.yml 초안

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 3s
      timeout: 3s
      retries: 20

  seed:
    build: ./data
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    restart: "no"

  service:
    build: ./service
    env_file: .env
    volumes:
      - ./ontology:/app/ontology:ro
    ports:
      - "8001:8000"
    depends_on:
      seed:
        condition: service_completed_successfully

  app:
    build: ./app
    env_file: .env
    environment:
      ONTOLOGY_API_URL: http://service:8000
    ports:
      - "8502:8501"
    depends_on:
      - service

  eval:
    build: ./service
    env_file: .env
    command: ["python", "-m", "eval.run_eval"]
    profiles: ["eval"]
    depends_on:
      - service

volumes:
  pgdata:
```

seed는 테이블이 이미 있으면 건너뛰도록 작성해, 재실행 시 데이터가 중복 적재되지 않게 함.

### 11-3. 환경변수 (.env.example)

```
POSTGRES_USER=bank
POSTGRES_PASSWORD=bank
POSTGRES_DB=bank
DATABASE_URL=postgresql+psycopg://bank:bank@db:5432/bank

LLM_PROVIDER=openai
LLM_MODEL=
LLM_API_KEY=
```

`.env`는 `.gitignore`에 추가해 API 키가 저장소에 올라가지 않게 함.

### 11-4. 실행 방법

Windows (PowerShell)

```powershell
copy .env.example .env
# .env 파일에 LLM_API_KEY 입력
docker compose up --build
```

macOS / Linux

```bash
cp .env.example .env
# .env 파일에 LLM_API_KEY 입력
docker compose up --build
```

| 접속 | 주소 |
| --- | --- |
| 데모 화면 | http://localhost:8502 |
| API 문서 (Swagger) | http://localhost:8001/docs |

| 작업 | 명령 |
| --- | --- |
| 종료 | `docker compose down` |
| 데이터까지 초기화 | `docker compose down -v` |
| 평가 실행 | `docker compose --profile eval run --rm eval` |
| 테스트 실행 | `docker compose run --rm service pytest` |

`docker-compose`(하이픈) 명령은 구버전 Compose v1 표기이며, 현재 Docker Desktop에서는 `docker compose`(공백)를 사용함.

---

## 12. 기술 스택

| 영역 | 기술 | 선택 이유 |
| --- | --- | --- |
| DB | PostgreSQL 16 | JSONB로 승인 요청·실행 기록 저장, 발표 1(pgvector) 확장 여지 |
| 서비스 | Python, FastAPI, SQLAlchemy Core, Pydantic | 자동 API 문서, 정의 검증, 파라미터 바인딩 SQL 생성 |
| 온톨로지 정의 | YAML | 사람이 읽고 Git으로 버전 관리 가능 (OSI·MetricFlow와 같은 접근) |
| 에이전트 | LLM API function calling | 도구 기반 접근 강제 |
| 데모 | Streamlit | 짧은 기간에 채팅·패널 UI 구성 가능 |
| 실행 환경 | Docker, Docker Compose | 한 줄 실행, 팀원 간 환경 통일 |
| 테스트 | pytest | compiler·policy 단위 테스트 |

---

## 13. 일정

| 기간 | 작업 | 산출물 |
| --- | --- | --- |
| 11/17 ~ 11/21 | 저장소·Docker 세팅, 목데이터 PostgreSQL 이관, `ontology.yaml` 1차 설계, API 명세 확정 | `docker compose up`으로 db·seed 동작, ontology.yaml v0.1 |
| 11/22 ~ 11/27 | compiler·query·policy·actions 구현, 에이전트 도구 연결, 단위 테스트 | 시나리오 1~6이 API·에이전트로 동작 |
| 11/28 ~ 11/30 | Streamlit 데모, 실행 기록 패널, (여유 시) 평가 | 데모 화면 완성 |
| 12/1 | 리허설, 녹화 백업, 발표자료, README | 발표 준비 완료 |
| 12/2 | 19주차 세션 발표·시연 | — |
| 12/5 | 공동세션 발표 (시연 없음, 캡처 사용) | — |

---

## 14. 역할 분담

| 담당 | 비중 | 맡는 부분 |
| --- | --- | --- |
| 지인 | 약 50% | 저장소·Docker 구성, `ontology.yaml` 구조 설계, Ontology Service(metadata, compiler, query, policy, actions), API 명세, 통합, PR 리뷰·머지 |
| 지영 | 약 25% | `schema.sql`, 목데이터 PostgreSQL 이관(seed), `ontology.yaml` 실제 매핑, 정의 검증, 평가 질문셋, A/B/C 비교 평가 |
| 수민 | 약 25% | 에이전트 도구·루프(LLM 연결), 실행 기록(trace), Streamlit 화면, 시연 리허설·영상, 발표자료 초안 |

세부 작업과 마감은 `docs/issues.md` 참고.

---

## 15. 협업 규칙

### 15-1. 브랜치

| 브랜치 | 용도 |
| --- | --- |
| main | 항상 `docker compose up`으로 실행 가능한 상태 유지 |
| feat/파트명-기능 | 기능 개발, PR로 main에 병합 (예: `feat/service-compiler`) |

### 15-2. 커밋 메시지

| 타입 | 용도 |
| --- | --- |
| feat | 새 기능 |
| fix | 버그 수정 |
| refactor | 동작 유지, 구조 개선 |
| style | 줄 맞춤·공백 등 동작과 무관한 수정 |
| docs | README 등 문서 |
| test | 테스트 추가·수정 |
| chore | 빌드 설정, 라이브러리 추가, 폴더 정리 |

예: `feat: owns 링크의 명의인 조건 컴파일 추가`, `chore: docker-compose에 seed 서비스 추가`

---

## 16. 리스크와 대응

| 리스크 | 대응 |
| --- | --- |
| 일정 부족 (실제 기간 약 2주) | MVP 범위 고정, 평가·MCP·Neo4j는 MVP 완료 후에만 진행 |
| compiler 구현 난이도 | 링크 경로를 최대 2단계로 제한, 지표는 YAML에 SQL 조각을 허용하는 방식으로 단순화 가능 |
| LLM 응답 비결정성 | 시연 질문 사전 반복 검증, temperature 0, 녹화 백업 |
| API 키·비용 | 팀 공용 키 사용 여부 결정, 시연용 호출 수 제한 |
| 네트워크 문제 | 시연 PC에서 사전 빌드(`docker compose build`) 완료, 녹화 백업 |

---

## 17. 확정 필요 사항

| 항목 | 내용 |
| --- | --- |
| 팀장·역할 분담 | 14장 담당자 확정 |
| LLM 제공자·모델 | 팀 내 사용 가능한 API 키 기준으로 결정 |
| 원천 컬럼명 | 기존 `generate_mock_data.py` 기준으로 4-2 예시 수정 |
| 지표 정의 | 기존 `semantic_definitions.yaml`의 활성 고객·순입금액 정의 이관 |
| 시연 방식 | 라이브 필수 여부를 공지 담당자에게 확인 |
