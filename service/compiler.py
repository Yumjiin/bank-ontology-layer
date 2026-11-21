"""온톨로지 요청을 SQL로 변환함 (Foundry의 Object Set Service 중 쿼리 생성 부분).

LLM은 SQL을 쓰지 않고 "객체 타입 + 속성 + 사람이 쓰는 값"만 보냄.
이 모듈이 ontology.yaml 정의를 보고 원천 테이블·컬럼·코드값으로 바꿔 SQL 조건을 만듦.

현재 구현 범위 (이슈 #3)
1. 값 변환: 사람이 쓰는 값 → 원천 코드      VIP → '01', 입금 → 'IN'
2. 날짜 변환: ISO 날짜 → 원천 저장 형식      2026-07-01 → '20260701'
3. 필터 → SQL 조건                           eq, in, gte, lte, between
4. 기본 필터 자동 적용                       Transaction 조회 시 cncl_yn = 'N'
5. 객체 검색 SELECT 조립                     compile_search()
+  역변환: 조회 결과를 사람이 쓰는 값으로     '01' → VIP (policy·응답에서 사용)

다음 단계 (TODO)
6. 링크 탐색, 지표 계산 (#4)
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, column, select, table
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import Select, TableClause

from metadata import ObjectType, Ontology, Property
from schemas import Filter


class CompileError(ValueError):
    """요청이 온톨로지 정의와 맞지 않을 때 발생함.

    API에서는 400으로 바꿔 반환하고, 메시지는 에이전트가 읽고 다시 시도할 수 있게
    "무엇이 틀렸고 무엇이 허용되는지"를 담음.
    """


# ---------------------------------------------------------------
# 정의 조회
# ---------------------------------------------------------------

def get_object_type(ontology: Ontology, name: str) -> ObjectType:
    obj = ontology.object_types.get(name)
    if obj is None:
        allowed = ", ".join(ontology.object_types)
        raise CompileError(f"존재하지 않는 객체 타입 '{name}'. 사용 가능: {allowed}")
    return obj


def get_property(obj_name: str, obj: ObjectType, prop_name: str) -> Property:
    prop = obj.properties.get(prop_name)
    if prop is None:
        allowed = ", ".join(obj.properties)
        raise CompileError(f"{obj_name}에 속성 '{prop_name}'이 없음. 사용 가능: {allowed}")
    return prop


def to_table(obj: ObjectType) -> TableClause:
    """객체 타입을 SQLAlchemy 테이블 객체로 만듦. 정의된 속성 컬럼만 포함함."""
    return table(obj.table, *(column(p.column) for p in obj.properties.values()))


# ---------------------------------------------------------------
# 1~2. 값 변환 (사람이 쓰는 값 → 원천 값)
# ---------------------------------------------------------------

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# ontology.yaml의 format 표기 → strftime 형식
_DATE_TOKENS = (("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d"))


def _strftime_pattern(fmt: str) -> str:
    for token, directive in _DATE_TOKENS:
        fmt = fmt.replace(token, directive)
    return fmt


def _parse_iso_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # date.fromisoformat은 Python 3.11부터 '20260701'도 받아주므로 형식을 먼저 엄격히 확인함
    text = str(value)
    if not _ISO_DATE.fullmatch(text):
        raise CompileError(f"{label}: 날짜는 YYYY-MM-DD 형식이어야 함 (받은 값: {value!r})")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise CompileError(f"{label}: 존재하지 않는 날짜 (받은 값: {value!r})") from None


def encode_value(obj_name: str, prop_name: str, prop: Property, value: Any) -> Any:
    """필터 값 하나를 원천 DB에 저장된 형태로 바꿈."""
    label = f"{obj_name}.{prop_name}"

    # 코드값: values의 오른쪽(사람이 쓰는 값)을 받아 왼쪽(원천 코드)으로 바꿈
    if prop.values:
        if isinstance(value, bool):
            value = str(value).lower()  # True → "true" (cancelled 같은 Y/N 코드용)
        reverse = {human: code for code, human in prop.values.items()}
        key = str(value)
        if key not in reverse:
            allowed = ", ".join(reverse)
            raise CompileError(f"{label}: 정의에 없는 값 '{value}'. 사용 가능: {allowed}")
        return reverse[key]

    if prop.type == "date":
        parsed = _parse_iso_date(value, label)
        return parsed.strftime(_strftime_pattern(prop.format)) if prop.format else parsed

    if prop.type == "number":
        if isinstance(value, bool):
            raise CompileError(f"{label}: 숫자가 필요함 (받은 값: {value!r})")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise CompileError(f"{label}: 숫자가 필요함 (받은 값: {value!r})") from None
        return int(number) if number.is_integer() else number

    return str(value)


# ---------------------------------------------------------------
# 역변환 (원천 값 → 사람이 쓰는 값)
# ---------------------------------------------------------------

def decode_value(prop: Property, raw: Any) -> Any:
    if raw is None:
        return None
    if prop.values:
        return prop.values.get(str(raw), raw)  # 정의에 없는 코드는 그대로 둠 (정합성 검증 #7에서 확인)
    if prop.type == "date" and prop.format:
        try:
            return datetime.strptime(str(raw), _strftime_pattern(prop.format)).date().isoformat()
        except ValueError:
            return raw
    return raw


def decode_row(obj: ObjectType, row: dict[str, Any]) -> dict[str, Any]:
    """{컬럼명: 원천 값} → {속성명: 사람이 쓰는 값}. 정의되지 않은 컬럼은 버림."""
    return {
        prop_name: decode_value(prop, row[prop.column])
        for prop_name, prop in obj.properties.items()
        if prop.column in row
    }


# ---------------------------------------------------------------
# 3. 필터 → SQL 조건
# ---------------------------------------------------------------

def build_condition(
    ontology: Ontology,
    obj_name: str,
    flt: Filter,
    tbl: TableClause | None = None,
) -> ColumnElement[bool]:
    """필터 하나를 SQL 조건으로 바꿈. 값은 모두 바인드 파라미터로 들어가 SQL 인젝션이 불가능함.

    tbl: 한 쿼리 안에서는 같은 테이블 객체를 써야 FROM 절이 중복되지 않음.
         compile_search처럼 SELECT를 조립할 때 넘겨주고, 단독 사용 시에는 생략함.
    """
    obj = get_object_type(ontology, obj_name)
    prop = get_property(obj_name, obj, flt.property)
    col = (tbl if tbl is not None else to_table(obj)).c[prop.column]

    def enc(v: Any) -> Any:
        return encode_value(obj_name, flt.property, prop, v)

    if flt.op == "eq":
        return col == enc(flt.value)

    if flt.op == "in":
        if not isinstance(flt.value, list) or not flt.value:
            raise CompileError(f"{obj_name}.{flt.property}: in 연산자는 값 목록이 필요함")
        return col.in_([enc(v) for v in flt.value])

    if flt.op == "gte":
        return col >= enc(flt.value)

    if flt.op == "lte":
        return col <= enc(flt.value)

    if flt.op == "between":
        if not isinstance(flt.value, list) or len(flt.value) != 2:
            raise CompileError(f"{obj_name}.{flt.property}: between 연산자는 [시작, 끝] 두 값이 필요함")
        start, end = (enc(v) for v in flt.value)
        return col.between(start, end)

    raise CompileError(f"지원하지 않는 연산자 '{flt.op}'")  # schemas.Filter가 먼저 막지만 방어용


def build_conditions(
    ontology: Ontology,
    obj_name: str,
    filters: list[Filter],
    tbl: TableClause | None = None,
) -> list[ColumnElement[bool]]:
    return [build_condition(ontology, obj_name, f, tbl) for f in filters]


# ---------------------------------------------------------------
# 4. 기본 필터 (default_filters)
# ---------------------------------------------------------------

# ontology.yaml의 default_filters 한 줄: "<속성> <연산자> <값>"  예) "cancelled = false"
_DEFAULT_FILTER = re.compile(r"^\s*(\w+)\s*(=|!=)\s*(.+?)\s*$")


def parse_default_filter(obj_name: str, expr: str) -> Filter:
    """기본 필터 문자열을 Filter로 바꿈. 값은 사람이 쓰는 값으로 적혀 있으므로 일반 필터와 같은 변환을 거침."""
    m = _DEFAULT_FILTER.match(expr)
    if not m:
        raise CompileError(f"{obj_name}.default_filters: 해석할 수 없는 식 '{expr}' (형식: '속성 = 값')")
    prop_name, op, value = m.groups()
    if op == "!=":
        # 현재 정의에 필요한 건 '='뿐이라 미지원. 필요해지면 Filter에 neq 연산자를 추가함
        raise CompileError(f"{obj_name}.default_filters: '!='는 아직 지원하지 않음 ('{expr}')")
    return Filter(property=prop_name, op="eq", value=value.strip("'\""))


def effective_filters(ontology: Ontology, obj_name: str, filters: list[Filter]) -> list[Filter]:
    """요청 필터에 기본 필터를 더함.

    같은 속성을 요청에서 직접 지정했다면 기본 필터는 빼고 요청을 따름.
    예) 기본은 취소 거래 제외지만, "취소된 거래만 보여줘"(cancelled = true)는 그대로 허용.
    """
    obj = get_object_type(ontology, obj_name)
    requested = {f.property for f in filters}
    defaults = [parse_default_filter(obj_name, d) for d in obj.default_filters]
    return list(filters) + [d for d in defaults if d.property not in requested]


def check_default_filters(ontology: Ontology) -> list[str]:
    """모든 default_filters가 해석·변환 가능한지 확인하고 오류 목록을 반환함.

    서비스 기동 시 호출함. 잘못된 기본 필터가 첫 요청 때가 아니라 기동 시점에 드러나게 하기 위함.
    """
    errors: list[str] = []
    for obj_name, obj in ontology.object_types.items():
        for expr in obj.default_filters:
            try:
                build_condition(ontology, obj_name, parse_default_filter(obj_name, expr))
            except CompileError as e:
                errors.append(str(e))
    return errors


# ---------------------------------------------------------------
# 5. 객체 검색 SELECT 조립
# ---------------------------------------------------------------

def primary_key_property(obj_name: str, obj: ObjectType) -> str:
    """primary_key 컬럼에 대응하는 속성 이름. 정의 검증에서 존재가 보장됨."""
    for name, prop in obj.properties.items():
        if prop.column == obj.primary_key:
            return name
    raise CompileError(f"{obj_name}: primary_key '{obj.primary_key}'에 대응하는 속성이 없음")


def compile_search(
    ontology: Ontology,
    obj_name: str,
    filters: list[Filter] | None = None,
    properties: list[str] | None = None,
    limit: int = 20,
) -> Select:
    """객체 검색 요청을 SELECT 문으로 바꿈.

    - properties를 생략하면 정의된 속성 전체를 조회함
    - 결과를 식별할 수 있게 기본 키 속성은 항상 포함함
    - 결과 순서가 실행마다 바뀌지 않게 기본 키로 정렬함 (시연·평가 재현성)
    - 결과 컬럼은 원천 컬럼명 그대로이며, decode_row로 사람이 쓰는 값으로 바꿈
    """
    obj = get_object_type(ontology, obj_name)
    tbl = to_table(obj)
    pk = primary_key_property(obj_name, obj)

    selected = list(obj.properties) if not properties else list(dict.fromkeys(properties))
    for name in selected:
        get_property(obj_name, obj, name)  # 없는 속성이면 허용 목록과 함께 오류
    if pk not in selected:
        selected.insert(0, pk)

    columns = [tbl.c[obj.properties[name].column] for name in selected]
    conditions = build_conditions(ontology, obj_name, effective_filters(ontology, obj_name, filters or []), tbl)

    stmt = select(*columns).select_from(tbl)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    return stmt.order_by(tbl.c[obj.primary_key]).limit(limit)