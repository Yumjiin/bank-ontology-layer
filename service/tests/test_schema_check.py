"""DB 정합성 검증 테스트. 메모리 SQLite에 테스트 온톨로지와 맞는 스키마를 만들고, 하나씩 어긋나게 바꿔 확인함."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from metadata import load_ontology
from schema_check import check_database

FIXTURE = Path(__file__).parent / "fixtures" / "test_ontology.yaml"

TABLES = {
    "cust": "cust_no TEXT PRIMARY KEY, cust_nm TEXT, cust_grd_cd TEXT",
    "acct": "acct_no TEXT PRIMARY KEY, acct_stat_cd TEXT",
    "acct_rel": "cust_no TEXT, acct_no TEXT, rel_cd TEXT",
    "txn": "txn_no INTEGER PRIMARY KEY, acct_no TEXT, txn_dt TEXT, txn_cd TEXT, amt INTEGER, cncl_yn TEXT",
}


@pytest.fixture(scope="module")
def onto():
    return load_ontology(FIXTURE)


def make_engine(**override):
    """TABLES에서 override한 테이블만 바꿔서 만듦. 값이 None이면 그 테이블을 만들지 않음."""
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        for name, cols in {**TABLES, **override}.items():
            if cols is not None:
                conn.execute(text(f"CREATE TABLE {name} ({cols})"))
    return eng


def test_matching_schema_passes(onto):
    assert check_database(make_engine(), onto) == []


def test_missing_table(onto):
    errors = check_database(make_engine(acct_rel=None), onto)
    # 관계 테이블을 쓰는 링크 두 개(owns, delegated)에서 각각 알려줌
    assert len(errors) == 2
    assert all("DB에 없는 테이블 'acct_rel'" in e for e in errors)


def test_missing_column_with_hint(onto):
    errors = check_database(make_engine(cust="cust_no TEXT PRIMARY KEY, cust_name TEXT, cust_grd_cd TEXT"), onto)
    assert errors == ["Customer.name: DB에 없는 컬럼 cust.cust_nm (비슷한 이름: cust_name, cust_no)"]


def test_number_column_without_number_type(onto):
    """txn_no 사례: DB는 INTEGER인데 정의에 type: number가 없으면 문자열로 비교돼 PostgreSQL에서 500이 남."""
    onto2 = onto.model_copy(deep=True)
    onto2.object_types["Transaction"].properties["txn_no"].type = "string"
    errors = check_database(make_engine(), onto2)
    assert len(errors) == 1
    assert "Transaction.txn_no" in errors[0]
    assert "타입은 숫자, 정의는 문자열로 비교함" in errors[0]
    assert "`type: number`로 지정" in errors[0]


def test_text_column_with_number_type(onto):
    errors = check_database(
        make_engine(txn="txn_no INTEGER PRIMARY KEY, acct_no TEXT, txn_dt TEXT, txn_cd TEXT, amt TEXT, cncl_yn TEXT"),
        onto,
    )
    assert len(errors) == 1
    assert "Transaction.amount" in errors[0] and "타입은 문자열, 정의는 숫자로 비교함" in errors[0]


def test_date_column_with_string_format(onto):
    """format(YYYYMMDD)이 있으면 문자열 날짜로 비교하므로 DB가 DATE 타입이면 어긋남."""
    errors = check_database(
        make_engine(txn="txn_no INTEGER PRIMARY KEY, acct_no TEXT, txn_dt DATE, txn_cd TEXT, amt INTEGER, cncl_yn TEXT"),
        onto,
    )
    assert len(errors) == 1
    assert "Transaction.date" in errors[0] and "format은 지움" in errors[0]


def test_link_key_type_mismatch(onto):
    errors = check_database(make_engine(acct_rel="cust_no TEXT, acct_no INTEGER, rel_cd TEXT"), onto)
    assert len(errors) == 2  # owns, delegated
    assert all("조인 컬럼 타입이 다름 acct.acct_no(문자열) = acct_rel.acct_no(숫자)" in e for e in errors)


def test_missing_via_condition_column(onto):
    errors = check_database(make_engine(acct_rel="cust_no TEXT, acct_no TEXT"), onto)
    assert errors == [
        "link_types.owns: DB에 없는 컬럼 acct_rel.rel_cd",
        "link_types.delegated: DB에 없는 컬럼 acct_rel.rel_cd",
    ]


def test_missing_join_key(onto):
    errors = check_database(
        make_engine(txn="txn_no INTEGER PRIMARY KEY, account TEXT, txn_dt TEXT, txn_cd TEXT, amt INTEGER, cncl_yn TEXT"),
        onto,
    )
    assert errors == [
        "Transaction.acct_no: DB에 없는 컬럼 txn.acct_no (비슷한 이름: txn_no)",
        "link_types.has_transaction.join.to_key: DB에 없는 컬럼 txn.acct_no (비슷한 이름: txn_no)",
    ]


def test_system_tables(onto):
    eng = make_engine(action_request="request_id INTEGER, status TEXT")
    errors = check_database(eng, onto, {"action_request": ["request_id", "status", "decided_at"], "agent_trace": ["id"]})
    assert errors == [
        "시스템 테이블: DB에 없는 컬럼 action_request.decided_at",
        "시스템 테이블: DB에 없는 테이블 'agent_trace'",
    ]
