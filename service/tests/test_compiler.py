"""compiler 단위 테스트. DB 없이 생성된 SQL 문자열만 검사함."""

from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from compiler import (
    CompileError,
    build_condition,
    decode_row,
    encode_value,
)
from metadata import load_ontology
from schemas import Filter

FIXTURE = Path(__file__).parent / "fixtures" / "test_ontology.yaml"


@pytest.fixture(scope="module")
def onto():
    return load_ontology(FIXTURE)


def sql(expr) -> str:
    """조건식을 값이 채워진 PostgreSQL 문자열로 바꿔 비교하기 쉽게 함."""
    return str(expr.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def cond(onto, obj, prop, op, value):
    return sql(build_condition(onto, obj, Filter(property=prop, op=op, value=value)))


# ---------------------------------------------------------------
# 1. 코드값 변환
# ---------------------------------------------------------------

class TestCodeValues:
    def test_human_value_becomes_source_code(self, onto):
        grade = onto.object_types["Customer"].properties["grade"]
        assert encode_value("Customer", "grade", grade, "VIP") == "01"

    def test_transaction_type(self, onto):
        t = onto.object_types["Transaction"].properties["type"]
        assert encode_value("Transaction", "type", t, "입금") == "IN"

    def test_boolean_code(self, onto):
  git commit -m "feat: 필터 값 변환과 SQL 조건 생성 추가"      c = onto.object_types["Transaction"].properties["cancelled"]
        assert encode_value("Transaction", "cancelled", c, False) == "N"
        assert encode_value("Transaction", "cancelled", c, "true") == "Y"

    def test_unknown_value_is_rejected_with_allowed_list(self, onto):
        # 발표 데모 A에서 LLM이 지어낸 'DEPOSIT' 같은 값을 여기서 차단함
        t = onto.object_types["Transaction"].properties["type"]
        with pytest.raises(CompileError) as e:
            encode_value("Transaction", "type", t, "DEPOSIT")
        assert "입금" in str(e.value)

    def test_source_code_itself_is_not_accepted(self, onto):
        # 에이전트는 사람이 쓰는 값만 보내야 함. 'IN'을 직접 보내면 거부
        t = onto.object_types["Transaction"].properties["type"]
        with pytest.raises(CompileError):
            encode_value("Transaction", "type", t, "IN")


# ---------------------------------------------------------------
# 2. 날짜·숫자 변환
# ---------------------------------------------------------------

class TestDateAndNumber:
    def test_iso_date_becomes_storage_format(self, onto):
        d = onto.object_types["Transaction"].properties["date"]
        assert encode_value("Transaction", "date", d, "2026-07-01") == "20260701"

    def test_wrong_date_format_is_rejected(self, onto):
        # 발표 데모 A의 날짜 형식 오류를 반대 방향에서 막음: 입력은 ISO만 허용
        d = onto.object_types["Transaction"].properties["date"]
        with pytest.raises(CompileError):
            encode_value("Transaction", "date", d, "20260701")
        with pytest.raises(CompileError):
            encode_value("Transaction", "date", d, "2026-02-30")  # 형식은 맞지만 없는 날짜

    def test_number(self, onto):
        a = onto.object_types["Transaction"].properties["amount"]
        assert encode_value("Transaction", "amount", a, "10000") == 10000
        with pytest.raises(CompileError):
            encode_value("Transaction", "amount", a, "만원")


# ---------------------------------------------------------------
# 3. 필터 → SQL 조건
# ---------------------------------------------------------------

class TestConditions:
    def test_eq(self, onto):
        assert cond(onto, "Customer", "grade", "eq", "VIP") == "cust.cust_grd_cd = '01'"

    def test_in(self, onto):
        assert cond(onto, "Transaction", "type", "in", ["입금", "출금"]) == "txn.txn_cd IN ('IN', 'OUT')"

    def test_between_dates(self, onto):
        assert (
            cond(onto, "Transaction", "date", "between", ["2026-07-01", "2026-09-30"])
            == "txn.txn_dt BETWEEN '20260701' AND '20260930'"
        )

    def test_gte_lte(self, onto):
        assert cond(onto, "Transaction", "amount", "gte", 10000) == "txn.amt >= 10000"
        assert cond(onto, "Transaction", "date", "lte", "2026-09-30") == "txn.txn_dt <= '20260930'"

    def test_values_are_bound_parameters(self, onto):
        # literal_binds 없이 컴파일하면 값 자리가 파라미터로 남아야 함 (SQL 인젝션 방지)
        expr = build_condition(onto, "Customer", Filter(property="grade", op="eq", value="VIP"))
        assert "%(" in str(expr.compile(dialect=postgresql.dialect()))

    def test_unknown_object(self, onto):
        with pytest.raises(CompileError, match="객체 타입"):
            cond(onto, "Loan", "grade", "eq", "VIP")

    def test_unknown_property(self, onto):
        with pytest.raises(CompileError, match="속성"):
            cond(onto, "Customer", "age", "eq", 30)

    def test_in_requires_list(self, onto):
        with pytest.raises(CompileError):
            cond(onto, "Transaction", "type", "in", "입금")

    def test_between_requires_two_values(self, onto):
        with pytest.raises(CompileError):
            cond(onto, "Transaction", "date", "between", ["2026-07-01"])


# ---------------------------------------------------------------
# 역변환
# ---------------------------------------------------------------

def test_decode_row(onto):
    row = {"txn_no": 1, "acct_no": "A1", "txn_dt": "20260915", "txn_cd": "OUT", "amt": 5000, "cncl_yn": "N", "extra": "x"}
    assert decode_row(onto.object_types["Transaction"], row) == {
        "txn_no": 1,
        "acct_no": "A1",
        "date": "2026-09-15",
        "type": "출금",
        "amount": 5000,
        "cancelled": "false",
    }
