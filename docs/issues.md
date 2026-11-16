# 작업 목록 (GitHub Issues 등록용)

각 항목을 GitHub Issue 하나로 등록함. 제목 앞의 `[파트]`는 라벨로 대신해도 됨.

---

## 지인 — 시스템

| # | 제목 | 완료 기준 | 마감 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | [setup] 저장소 뼈대와 Docker 구성 | `docker compose up --build` 시 db·seed·service·app이 모두 기동되고 `/health`, 데모 화면 접속 가능 | 11/19 | 없음 |
| 2 | [setup] ontology.yaml 구조와 API 계약 공유 | `ontology.yaml` 키 설명, `docs/api-contract.md`, `docs/fixtures/` 공유 완료 | 11/19 | 없음 |
| 3 | [service] compiler: 객체 검색과 필터 → SQL 변환 | Customer·Account·Transaction 검색이 필터(eq, in, between)와 코드값·날짜 변환을 거쳐 실행됨, 단위 테스트 통과 | 11/24 | 지영 #1 |
| 4 | [service] compiler: 링크 탐색과 지표 계산 | owns·has_transaction 경로 탐색, active_customers=165, net_deposit=389,638,000 | 11/25 | #3 |
| 5 | [service] policy: 역할별 마스킹·차단 | marketer 조회 시 PII 속성 마스킹, 응답에 policy 필드 포함 | 11/26 | #3 |
| 6 | [service] actions: 승인 요청·승인·거절 | teller 요청 시 403, manager 요청 시 PENDING 생성, 승인 시 EXECUTED | 11/27 | #5 |
| 7 | [service] DB 정합성 검증 | 기동 시 ontology.yaml의 테이블·컬럼이 실제 DB에 있는지 확인 | 11/27 | 지영 #1 |
| 8 | [integration] 에이전트·서비스 연결과 시나리오 점검 | 시연 시나리오 1~6이 화면에서 동작 | 11/28 | 수민 #3 |

---

## 지영 — 데이터·온톨로지 정의·평가

| # | 제목 | 완료 기준 | 마감 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | [data] 원천 테이블 스키마와 목데이터 seed | `data/schema.sql`에 5개 테이블 작성, `docker compose up` 시 자동 적재, 재실행해도 중복 없음, 행 수 일치 (cust 300, acct 317, acct_rel 362, txn 12,908) | 11/21 | 지인 #1 |
| 2 | [ontology] ontology.yaml 실제 매핑 작성 | TODO(지영) 표시 모두 실제 컬럼명·코드값으로 교체, `pytest` 통과 | 11/21 | #1 |
| 3 | [ontology] 코드값 정합성 확인 스크립트 | ontology.yaml의 values가 실제 데이터 DISTINCT 값을 모두 포함하는지 출력 | 11/24 | #2 |
| 4 | [eval] 평가 질문셋 작성 | `eval/questions.yaml` 20~30문항, 각 문항의 정답 SQL과 정답 값 | 11/24 | #1 |
| 5 | [eval] 조건 A·B 평가 자동화 | 질문별로 LLM이 SQL을 생성하고 실행해 정답과 비교, 조건별 3회, 정답률 출력 | 11/27 | #4 |
| 6 | [eval] 조건 C 평가와 결과 정리 | 에이전트 경유 정답률 측정, A·B·C 비교 표와 그래프, 발표용 정리 | 12/1 | #5, 지인 #8 |

---

## 수민 — 에이전트·데모·시연

| # | 제목 | 완료 기준 | 마감 | 의존성 |
| --- | --- | --- | --- | --- |
| 1 | [agent] LLM 연결과 도구 정의 | 사용할 LLM 확정, `agent/tools.py`의 5개 도구를 function calling 형식으로 정의 | 11/21 | 지인 #2 |
| 2 | [agent] 에이전트 루프 (fixture 모드) | `docs/fixtures` 응답으로 "3분기 활성 고객 수는?"에 get_metric을 호출해 답변 | 11/24 | #1 |
| 3 | [agent] 실제 API 연결과 실행 기록 저장 | fixture 대신 서비스 호출, 각 단계를 agent_trace에 기록 | 11/27 | 지인 #4 |
| 4 | [app] Streamlit 채팅과 역할 선택 | 역할별로 질문하고 답변·사용 도구가 화면에 표시됨 | 11/25 | #2 |
| 5 | [app] 승인 대기 목록과 실행 기록 패널 | manager 사이드바에서 승인·거절, 단계별 도구·SQL·정책 판정 표시 | 11/28 | #3 |
| 6 | [demo] 시연 리허설과 녹화 | 시나리오 1~7 리허설, 시연 영상 녹화, 12/5용 캡처 | 12/1 | #5 |
| 7 | [demo] 발표자료 초안 | 아키텍처·시연·평가 결과 포함 초안 | 12/1 | 지영 #6 |
