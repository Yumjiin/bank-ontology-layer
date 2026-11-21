"""compiler 단위 테스트. DB 없이 생성된 SQL 문자열만 검사함."""

from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from compiler import (
    CompileError,
    build_condition,
    check_default_filters,
    compile_search,
    decode_row,
    effective_filters,
    encode_value,
    parse_default_filter,
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
        c = onto.object_types["Transaction"].properties["cancelled"]
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


# ---------------------------------------------------------------
# 4. 기본 필터
# ---------------------------------------------------------------

class TestDefaultFilters:
    def test_parse(self):
        f = parse_default_filter("Transaction", "cancelled = false")
        assert (f.property, f.op, f.value) == ("cancelled", "eq", "false")

    def test_unparseable_expression(self):
        with pytest.raises(CompileError):
            parse_default_filter("Transaction", "cancelled is false")

    def test_default_is_added(self, onto):
        props = [f.property for f in effective_filters(onto, "Transaction", [])]
        assert props == ["cancelled"]

    def test_explicit_filter_overrides_default(self, onto):
        # "취소된 거래만" 같은 요청은 기본 필터에 막히지 않아야 함
        req = [Filter(property="cancelled", op="eq", value="true")]
        result = effective_filters(onto, "Transaction", req)
        assert len(result) == 1 and result[0].value == "true"

    def test_object_without_defaults(self, onto):
        assert effective_filters(onto, "Customer", []) == []

    def test_all_defaults_in_fixture_are_valid(self, onto):
        assert check_default_filters(onto) == []

    def test_invalid_default_value_is_reported(self, onto):
        broken = onto.model_copy(deep=True)
        broken.object_types["Transaction"].default_filters = ["cancelled = maybe"]
        assert check_default_filters(broken)


# ---------------------------------------------------------------
# 5. 객체 검색 SELECT
# ---------------------------------------------------------------

class TestSearch:
    def test_customer_search_with_filter(self, onto):
        stmt = compile_search(onto, "Customer", [Filter(property="grade", op="eq", value="VIP")], ["grade"], limit=5)
        assert sql(stmt) == (
            "SELECT cust.cust_no, cust.cust_grd_cd \n"
            "FROM cust \n"
            "WHERE cust.cust_grd_cd = '01' ORDER BY cust.cust_no \n"
            " LIMIT 5"
        )

    def test_transaction_search_applies_default_filter(self, onto):
        # 발표 데모의 '취소 거래 포함' 함정이 요청에 없어도 자동으로 제외됨
        stmt = compile_search(
            onto,
            "Transaction",
            [Filter(property="date", op="between", value=["2026-09-01", "2026-09-30"])],
            ["date", "type", "amount"],
        )
        s = sql(stmt)
        assert "txn.txn_dt BETWEEN '20260901' AND '20260930'" in s
        assert "txn.cncl_yn = 'N'" in s
        assert s.count("FROM txn") == 1  # 같은 테이블 객체를 공유해 FROM이 중복되지 않음

    def test_all_properties_when_not_specified(self, onto):
        s = sql(compile_search(onto, "Customer"))
        assert "cust.cust_no, cust.cust_nm, cust.cust_grd_cd" in s

    def test_primary_key_always_included(self, onto):
        s = sql(compile_search(onto, "Account", properties=["status"]))
        assert s.startswith("SELECT acct.acct_no, acct.acct_stat_cd")

    def test_duplicate_properties_are_removed(self, onto):
        s = sql(compile_search(onto, "Account", properties=["status", "status"]))
        assert s.count("acct.acct_stat_cd") == 1

    def test_unknown_property_in_select(self, onto):
        with pytest.raises(CompileError, match="속성"):
            compile_search(onto, "Customer", properties=["age"])