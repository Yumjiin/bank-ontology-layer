"""DB 정합성 검증 (설계서 4-3).

ontology.yaml이 가리키는 테이블·컬럼이 실제 DB에 있는지, 타입이 맞는지 서비스 기동 시 확인함.
정의 파일만 보고는 알 수 없는 오류(오타, 컬럼명 변경, 타입 불일치)를 첫 요청 때가 아니라
기동 시점에 드러내기 위함.

| 검사 | 예시 오류 |
| ---- | --------- |
| 테이블 존재 | object_types·via에 적힌 테이블이 DB에 없음 |
| 컬럼 존재 | 속성의 column, 링크의 키·조건 컬럼이 DB에 없음 |
| 속성 타입 | DB가 INTEGER인데 type을 지정하지 않음 (txn_no 사례: 문자열로 비교돼 500 오류) |
| 링크 키 타입 | 조인하는 두 컬럼의 타입이 다름 (TEXT = INTEGER 비교) |

DB 메타데이터는 SQLAlchemy inspect로 읽음 (PostgreSQL에서는 information_schema·pg_catalog를 조회함).
같은 코드가 테스트용 SQLite에서도 동작함.
코드값(values)이 실제 데이터를 모두 포함하는지는 data/check_codes.py에서 따로 확인함.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Iterable

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.types import Boolean, Date, DateTime, Integer, Numeric, String, TypeEngine

from metadata import VIA_CONDITION, Ontology, Property

KIND_LABEL = {"text": "문자열", "number": "숫자", "date": "날짜", "boolean": "불리언"}


def column_kind(sql_type: TypeEngine) -> str | None:
    """DB 컬럼 타입을 비교용 분류로 바꿈. 분류할 수 없는 타입은 None (타입 검사를 건너뜀)."""
    if isinstance(sql_type, (Integer, Numeric)):
        return "number"
    if isinstance(sql_type, String):
        return "text"
    if isinstance(sql_type, (Date, DateTime)):
        return "date"
    if isinstance(sql_type, Boolean):
        return "boolean"
    return None


def expected_kind(prop: Property) -> str:
    """속성 정의로 기대하는 DB 컬럼 분류. compiler.encode_value가 만드는 값의 형태와 같아야 함."""
    if prop.values:
        return "text"  # 코드값은 문자열 코드로 바뀌어 비교됨 (예: "01", "IN")
    if prop.type == "number":
        return "number"
    if prop.type == "date":
        return "text" if prop.format else "date"  # format이 있으면 'YYYYMMDD' 같은 문자열로 저장된 날짜
    return "text"


def _type_fix(prop: Property, actual: str) -> str:
    if actual == "number":
        return "`type: number`로 지정"
    if actual == "date":
        return "`type: date`로 지정하고 format은 지움"
    if prop.type == "date":
        return "문자열 날짜면 format(예: YYYYMMDD)을 지정"
    return "type을 지우거나 DB 컬럼 타입을 확인"


class _Schema:
    """DB 테이블별 컬럼과 분류. 같은 테이블을 여러 번 조회하지 않도록 캐시함."""

    def __init__(self, engine: Engine):
        self.inspector = inspect(engine)
        self.tables = set(self.inspector.get_table_names())
        self._columns: dict[str, dict[str, str | None]] = {}

    def columns(self, table: str) -> dict[str, str | None]:
        if table not in self._columns:
            self._columns[table] = {c["name"]: column_kind(c["type"]) for c in self.inspector.get_columns(table)}
        return self._columns[table]


def _hint(name: str, candidates: Iterable[str]) -> str:
    close = get_close_matches(name, list(candidates), n=3, cutoff=0.6)
    return f" (비슷한 이름: {', '.join(close)})" if close else ""


def check_database(
    engine: Engine, ontology: Ontology, extra_tables: dict[str, Iterable[str]] | None = None
) -> list[str]:
    """정합성 오류 목록을 반환함. 비어 있으면 통과.

    extra_tables: 온톨로지 밖에서 서비스가 쓰는 테이블과 컬럼 (예: action_request)
    DB에 연결할 수 없으면 sqlalchemy OperationalError가 그대로 올라감 (호출하는 쪽에서 처리).
    """
    db = _Schema(engine)
    errors: list[str] = []

    def table_ok(table: str, where: str) -> bool:
        if table in db.tables:
            return True
        errors.append(f"{where}: DB에 없는 테이블 '{table}'{_hint(table, db.tables)}")
        return False

    def column_ok(table: str, col: str, where: str) -> bool:
        cols = db.columns(table)
        if col in cols:
            return True
        errors.append(f"{where}: DB에 없는 컬럼 {table}.{col}{_hint(col, cols)}")
        return False

    # 1. 객체 타입: 테이블, 속성 컬럼, 속성 타입
    for obj_name, obj in ontology.object_types.items():
        if not table_ok(obj.table, f"object_types.{obj_name}"):
            continue
        column_ok(obj.table, obj.primary_key, f"{obj_name}.primary_key")
        for prop_name, prop in obj.properties.items():
            where = f"{obj_name}.{prop_name}"
            if not column_ok(obj.table, prop.column, where):
                continue
            actual = db.columns(obj.table)[prop.column]
            expected = expected_kind(prop)
            if actual is not None and actual != expected:
                errors.append(
                    f"{where}: DB 컬럼 {obj.table}.{prop.column}의 타입은 {KIND_LABEL[actual]}, "
                    f"정의는 {KIND_LABEL[expected]}로 비교함 → {_type_fix(prop, actual)}"
                )

    # 2. 링크: 관계 테이블·키 컬럼 존재, 조인하는 두 컬럼의 타입 일치
    def same_kind(where: str, left: tuple[str, str], right: tuple[str, str]) -> None:
        lk = db.columns(left[0]).get(left[1])
        rk = db.columns(right[0]).get(right[1])
        if lk and rk and lk != rk:
            errors.append(
                f"{where}: 조인 컬럼 타입이 다름 {left[0]}.{left[1]}({KIND_LABEL[lk]}) = "
                f"{right[0]}.{right[1]}({KIND_LABEL[rk]})"
            )

    for name, link in ontology.link_types.items():
        where = f"link_types.{name}"
        src = ontology.object_types[link.from_]
        dst = ontology.object_types[link.to]
        if src.table not in db.tables or dst.table not in db.tables:
            continue  # 객체 테이블이 없다는 오류는 위에서 이미 기록함

        if link.via:
            via = link.via
            if not table_ok(via.table, where):
                continue
            keys = [via.from_key, via.to_key]
            if via.condition:
                keys.append(VIA_CONDITION.match(via.condition).group(1))  # 형식은 metadata에서 검증됨
            if all([column_ok(via.table, k, where) for k in keys]):
                same_kind(where, (src.table, src.primary_key), (via.table, via.from_key))
                same_kind(where, (dst.table, dst.primary_key), (via.table, via.to_key))
        elif link.join:
            j = link.join
            ok_from = column_ok(src.table, j.from_key, f"{where}.join.from_key")
            ok_to = column_ok(dst.table, j.to_key, f"{where}.join.to_key")
            if ok_from and ok_to:
                same_kind(where, (src.table, j.from_key), (dst.table, j.to_key))

    # 3. 서비스가 직접 쓰는 시스템 테이블
    for table, cols in (extra_tables or {}).items():
        if table_ok(table, "시스템 테이블"):
            for col in cols:
                column_ok(table, col, "시스템 테이블")

    return errors
