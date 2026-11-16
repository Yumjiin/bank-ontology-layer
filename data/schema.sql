-- Bank Ontology Layer 스키마
-- seed 컨테이너가 기동 시 실행함. 여러 번 실행해도 안전하도록 IF NOT EXISTS 사용.

-- ---------------------------------------------------------------
-- 1. 원천 테이블 (담당: 지영)
--    발표 데모 generate_mock_data.py의 SQLite 스키마를 PostgreSQL로 옮겨서 작성함.
--    brnc, cust, acct, acct_rel, txn 5개 테이블
-- ---------------------------------------------------------------
-- TODO(지영): CREATE TABLE IF NOT EXISTS brnc (...);
-- TODO(지영): CREATE TABLE IF NOT EXISTS cust (...);
-- TODO(지영): CREATE TABLE IF NOT EXISTS acct (...);
-- TODO(지영): CREATE TABLE IF NOT EXISTS acct_rel (...);
-- TODO(지영): CREATE TABLE IF NOT EXISTS txn (...);


-- ---------------------------------------------------------------
-- 2. 시스템 테이블
-- ---------------------------------------------------------------

-- 액션 승인 요청 (담당: 지인)
CREATE TABLE IF NOT EXISTS action_request (
    request_id      BIGSERIAL PRIMARY KEY,
    action_type     TEXT        NOT NULL,
    target_type     TEXT        NOT NULL,
    target_id       TEXT        NOT NULL,
    params          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    requested_role  TEXT        NOT NULL,
    session_id      TEXT,
    status          TEXT        NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ
);

-- 실행 기록 (담당: 수민)
-- span_type: invoke_agent / chat / execute_tool (OpenTelemetry GenAI 구분을 따름)
CREATE TABLE IF NOT EXISTS agent_trace (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT        NOT NULL,
    session_id      TEXT        NOT NULL,
    step            INTEGER     NOT NULL,
    span_type       TEXT        NOT NULL
                    CHECK (span_type IN ('invoke_agent', 'chat', 'execute_tool')),
    tool_name       TEXT,
    input           JSONB,
    output_summary  TEXT,
    sql_text        TEXT,
    policy_decision TEXT,
    latency_ms      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_trace_session ON agent_trace (session_id, step);
