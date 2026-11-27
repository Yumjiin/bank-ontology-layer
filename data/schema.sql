-- Bank Ontology Layer 스키마
-- seed 컨테이너가 기동 시 실행함. 여러 번 실행해도 안전하도록 IF NOT EXISTS 사용.

-- ---------------------------------------------------------------
-- 1. 원천 테이블 (담당: 지영)
--    발표 데모 generate_mock_data.py의 SQLite 스키마를 PostgreSQL로 옮겨서 작성함.
--    brnc, cust, acct, acct_rel, txn 5개 테이블
--
--    작성 원칙: 원본의 "함정"을 고치지 않고 그대로 보존함 (design.md 3-1)
--    - 컬럼명·타입은 원본 SQLite DDL과 1:1로 맞춤 (attr1~attr5 같은 의미 없는 이름 포함)
--    - 날짜는 DATE가 아니라 'YYYYMMDD' 문자열(TEXT) 그대로 둠
--      날짜·시각 컬럼은 COLLATE "C"(글자 코드 순 비교)로 지정함. PostgreSQL 기본 정렬(en_US.utf8)은
--      '-' 같은 기호를 무시해 '2026-09-01'처럼 형식을 잘못 써도 맞아버림. 발표 데모(SQLite)와 같은 비교가 되도록 맞춤
--    - 코드값(A/D/C, 01/02, IN/OUT/INT/FEE, Y/N)도 원본 값 그대로 둠
--    → 이 함정들을 온톨로지 레이어가 해결하는 것을 보여주는 것이 프로젝트의 핵심이므로
--      DB 단계에서 미리 정리하지 않음
--    추가 컬럼: cust.cust_grd_cd (원본에 없음, seed.py에서 규칙 기반으로 계산해 채움)
-- ---------------------------------------------------------------

-- 지점. 컬럼 의미는 semantic_definitions.yaml에만 있음
--   attr1 지점명, attr2 지역코드, attr3 개설일(YYYYMMDD), attr4 직원 수, attr5 지점 유형
CREATE TABLE IF NOT EXISTS brnc (
    brnc_cd     TEXT PRIMARY KEY,
    attr1       TEXT,
    attr2       TEXT,
    attr3       TEXT COLLATE "C",
    attr4       INTEGER,
    attr5       TEXT
);

-- 고객
CREATE TABLE IF NOT EXISTS cust (
    cust_no     TEXT PRIMARY KEY,
    cust_nm     TEXT,              -- PII (marketer 역할에서 마스킹 대상)
    birth_ymd   TEXT COLLATE "C",  -- 'YYYYMMDD'
    sex_cd      TEXT,              -- 1 남성, 2 여성
    join_ymd    TEXT COLLATE "C",  -- 'YYYYMMDD'
    cust_grd_cd TEXT               -- 01 VIP, 02 일반 (원본에 없던 컬럼, seed.py의 규칙으로 계산)
);

-- 계좌
CREATE TABLE IF NOT EXISTS acct (
    acct_no      TEXT PRIMARY KEY,
    prod_cd      TEXT,             -- DD01 보통예금, SV01 정기적금
    open_ymd     TEXT COLLATE "C", -- 'YYYYMMDD'
    acct_stat_cd TEXT,             -- A 정상, D 휴면, C 해지
    brnc_cd      TEXT REFERENCES brnc(brnc_cd)
);

-- 계좌관계. 명의인과 대리인이 한 테이블에 섞여 있음 (함정)
CREATE TABLE IF NOT EXISTS acct_rel (
    acct_no     TEXT REFERENCES acct(acct_no),
    cust_no     TEXT REFERENCES cust(cust_no),
    rel_cd      TEXT,              -- 01 명의인, 02 대리인
    PRIMARY KEY (acct_no, cust_no)
);

-- 거래. 금액은 부호 없이 양수, 방향은 txn_cd로 구분함 (함정)
-- txn_id는 SERIAL이 아니라 seed가 원본(SQLite)과 같은 번호를 직접 넣음
--   → 원본 DB와 행 단위로 비교 가능하게 하기 위함
CREATE TABLE IF NOT EXISTS txn (
    txn_id      INTEGER PRIMARY KEY,
    acct_no     TEXT REFERENCES acct(acct_no),
    txn_dt      TEXT COLLATE "C",  -- 'YYYYMMDD'
    txn_tm      TEXT COLLATE "C",  -- 'HHMMSS'
    txn_cd      TEXT,              -- IN 입금, OUT 출금, INT 이자, FEE 수수료
    amt         INTEGER,           -- 항상 양수
    cncl_yn     TEXT,              -- Y 취소 (모든 집계에서 제외)
    chnl_cd     TEXT               -- 01 창구, 02 ATM, 03 인터넷, 04 모바일
);


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
