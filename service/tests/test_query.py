"""query 테스트. 메모리 SQLite에 작은 데이터를 넣어 실행까지 확인함.

PostgreSQL 없이 돌아가도록 SQLite를 쓰지만, compiler가 만드는 SQL은 표준 SELECT라 결과는 같음.
실제 seed 데이터로는 Swagger에서 확인함 (PR 본문의 확인 방법 참고).
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import query
from compiler import CompileError
from metadata import load_ontology
from schemas import Filter, LinkRequest, MetricRequest, Period, SearchRequest

FIXTURE = Path(__file__).parent / "fixtures" / "test_ontology.yaml"

SEED_SQL = """
CREATE TABLE cust (cust_no TEXT PRIMARY KEY, cust_nm TEXT, cust_grd_cd TEXT);
CREATE TABLE acct (acct_no TEXT PRIMARY KEY, acct_stat_cd TEXT);
CREATE TABLE acct_rel (cust_no TEXT, acct_no TEXT, rel_cd TEXT);
CREATE TABLE txn (txn_no INTEGER PRIMARY KEY, acct_no TEXT, txn_dt TEXT, txn_cd TEXT, amt INTEGER, cncl_yn TEXT);

INSERT INTO cust VALUES ('C01', '김하나', '01'), ('C02', '이두리', '02'), ('C03', '박세나', '01');
INSERT INTO acct VALUES ('A01', 'A'), ('A02', 'D');
-- C01은 A01의 명의인, C02는 A01의 대리인, C03은 A02의 명의인
INSERT INTO acct_rel VALUES ('C01', 'A01', '01'), ('C02', 'A01', '02'), ('C03', 'A02', '01');
INSERT INTO txn VALUES
  (1, 'A01', '20260901', 'IN',  100000, 'N'),
  (2, 'A01', '20260902', 'OUT',  30000, 'Y'),
  (3, 'A01', '20260915', 'INT',    500, 'N'),
  (4, 'A02', '20261001', 'IN',   50000, 'N'),
  (5, 'A01', '20260920', 'OUT',  20000, 'N');
"""


@pytest.fixture(scope="module")
def onto():
    return load_ontology(FIXTURE)


@pytest.fixture(scope="module")
def engine():
    # TestClient는 다른 스레드에서 앱을 실행하므로 연결 하나를 공유하도록 설정함
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as conn:
        for stmt in SEED_SQL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    return eng


# ---------------------------------------------------------------
# search_objects
# ---------------------------------------------------------------

def test_search_decodes_values(engine, onto):
    req = SearchRequest(object_type="Customer", filters=[Filter(property="grade", op="eq", value="VIP")])
    res = query.search_objects(engine, onto, req, role="teller")
    assert res["count"] == 2
    assert res["items"][0] == {"cust_no": "C01", "name": "김하나", "grade": "VIP"}
    assert res["has_more"] is False
    assert res["policy"] == {"masked": [], "blocked": []}
    assert res["trace_id"].startswith("t-")


def test_search_applies_default_filter(engine, onto):
    # 2번 거래는 취소(cncl_yn='Y')라 기본 필터로 빠져야 함
    req = SearchRequest(
        object_type="Transaction",
        filters=[Filter(property="date", op="between", value=["2026-09-01", "2026-09-30"])],
    )
    res = query.search_objects(engine, onto, req, role="teller")
    assert [i["txn_no"] for i in res["items"]] == [1, 3, 5]
    assert res["items"][0]["date"] == "2026-09-01"
    assert res["items"][0]["type"] == "입금"


def test_search_has_more_when_truncated(engine, onto):
    res = query.search_objects(engine, onto, SearchRequest(object_type="Customer", limit=2), role="teller")
    assert res["count"] == 2 and res["has_more"] is True


def test_search_total_ignores_limit(engine, onto):
    # "VIP 고객 수는?" 같은 질문은 결과를 다 받지 않고 total로 답함
    req = SearchRequest(object_type="Customer", filters=[Filter(property="grade", op="eq", value="VIP")], limit=1)
    res = query.search_objects(engine, onto, req, role="teller")
    assert res["count"] == 1 and res["total"] == 2 and res["has_more"] is True


def test_search_returns_readable_sql(engine, onto):
    req = SearchRequest(object_type="Customer", filters=[Filter(property="grade", op="eq", value="VIP")], limit=5)
    sql = query.search_objects(engine, onto, req, role="teller")["sql"]
    assert "cust_grd_cd = '01'" in sql and "LIMIT 5" in sql  # 응답에는 요청한 limit 그대로 보여줌 (+1 아님)


def test_search_invalid_value_raises(engine, onto):
    req = SearchRequest(object_type="Customer", filters=[Filter(property="grade", op="eq", value="GOLD")])
    with pytest.raises(CompileError):
        query.search_objects(engine, onto, req, role="teller")


# ---------------------------------------------------------------
# get_object
# ---------------------------------------------------------------

def test_get_object(engine, onto):
    res = query.get_object(engine, onto, "Account", "A02", role="teller")
    assert res["item"] == {"acct_no": "A02", "status": "휴면"}


def test_get_object_ignores_default_filter(engine, onto):
    # 취소된 거래도 ID로 직접 조회하면 보여야 함
    res = query.get_object(engine, onto, "Transaction", "2", role="teller")
    assert res["item"]["cancelled"] == "true"


def test_get_object_not_found(engine, onto):
    with pytest.raises(query.NotFoundError):
        query.get_object(engine, onto, "Customer", "C99", role="teller")


# ---------------------------------------------------------------
# API (오류 코드 매핑)
# ---------------------------------------------------------------

@pytest.fixture()
def client(engine, onto):
    local = Path(__file__).resolve().parents[2] / "ontology" / "ontology.yaml"
    os.environ.setdefault("ONTOLOGY_PATH", str(local if local.exists() else "/app/ontology/ontology.yaml"))
    from main import app

    with TestClient(app) as c:
        app.state.engine = engine  # 실제 DB 대신 테스트용 SQLite와 테스트 온톨로지 사용
        app.state.ontology = onto
        yield c


HEADERS = {"X-Role": "teller", "X-Session-Id": "test"}


def test_api_search(client):
    res = client.post("/objects/search", headers=HEADERS, json={
        "object_type": "Customer",
        "filters": [{"property": "grade", "op": "eq", "value": "VIP"}],
    })
    assert res.status_code == 200
    assert res.json()["count"] == 2


def test_api_invalid_request_is_400_with_allowed_values(client):
    res = client.post("/objects/search", headers=HEADERS, json={
        "object_type": "Customer",
        "filters": [{"property": "grade", "op": "eq", "value": "GOLD"}],
    })
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["code"] == "INVALID_REQUEST"
    assert "VIP" in detail["message"]  # 에이전트가 허용값을 보고 다시 시도할 수 있음


def test_api_get_object_and_404(client):
    assert client.get("/objects/Customer/C01", headers=HEADERS).json()["item"]["grade"] == "VIP"
    res = client.get("/objects/Customer/C99", headers=HEADERS)
    assert res.status_code == 404 and res.json()["detail"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------
# get_linked_objects
# ---------------------------------------------------------------

def link(engine, onto, obj, oid, name, *filters):
    return query.get_linked_objects(engine, onto, obj, oid, name, LinkRequest(filters=list(filters)), role="teller")


def test_link_owns_uses_via_condition(engine, onto):
    # 명의인 링크는 rel_cd = '01'만 따라감
    assert [i["acct_no"] for i in link(engine, onto, "Customer", "C01", "owns")["items"]] == ["A01"]
    assert link(engine, onto, "Customer", "C02", "owns")["total"] == 0


def test_link_delegated(engine, onto):
    res = link(engine, onto, "Customer", "C02", "delegated")
    assert res["object_type"] == "Account" and res["items"] == [{"acct_no": "A01", "status": "활성"}]


def test_link_applies_target_default_filter_and_filters(engine, onto):
    # 취소된 2번 거래는 빠지고, 필터는 도착 객체(Transaction) 기준
    assert link(engine, onto, "Account", "A01", "has_transaction")["total"] == 3
    res = link(engine, onto, "Account", "A01", "has_transaction", Filter(property="type", op="eq", value="입금"))
    assert res["total"] == 1 and res["items"][0]["txn_no"] == 1


def test_link_from_missing_object_is_not_found(engine, onto):
    with pytest.raises(query.NotFoundError):
        link(engine, onto, "Customer", "C99", "owns")


def test_link_must_start_from_object_type(engine, onto):
    with pytest.raises(CompileError, match="owns"):
        link(engine, onto, "Account", "A01", "owns")


# ---------------------------------------------------------------
# get_metric
# ---------------------------------------------------------------

def metric(engine, onto, name, start, end, *filters):
    req = MetricRequest(period=Period(start=start, end=end), filters=list(filters))
    return query.get_metric(engine, onto, name, req, role="teller")


def test_active_customers_counts_only_owners(engine, onto):
    # 9월 A01 거래의 명의인은 C01뿐 (C02는 대리인이라 제외)
    assert metric(engine, onto, "active_customers", "2026-09-01", "2026-09-30")["value"] == 1
    assert metric(engine, onto, "active_customers", "2026-09-01", "2026-10-31")["value"] == 2


def test_net_deposit_signs_and_excludes(engine, onto):
    # +100,000(입금) -20,000(출금). 취소된 출금 30,000과 이자 500은 제외
    res = metric(engine, onto, "net_deposit", "2026-09-01", "2026-09-30")
    assert res["value"] == 80000 and res["unit"] == "원"


def test_net_deposit_empty_period_is_zero(engine, onto):
    assert metric(engine, onto, "net_deposit", "2026-11-01", "2026-11-30")["value"] == 0


def test_metric_request_cannot_override_definition(engine, onto):
    with pytest.raises(CompileError, match="고정된 조건"):
        metric(engine, onto, "net_deposit", "2026-09-01", "2026-09-30", Filter(property="type", op="eq", value="이자"))


def test_api_link_and_metric(client):
    res = client.post("/objects/Customer/C01/links/owns", headers=HEADERS, json={})
    assert res.status_code == 200 and res.json()["total"] == 1
    res = client.post("/metrics/net_deposit", headers=HEADERS, json={"period": {"start": "2026-09-01", "end": "2026-09-30"}})
    assert res.status_code == 200 and res.json()["value"] == 80000
    res = client.post("/metrics/churn_rate", headers=HEADERS, json={"period": {"start": "2026-09-01", "end": "2026-09-30"}})
    assert res.status_code == 400 and "active_customers" in res.json()["detail"]["message"]
