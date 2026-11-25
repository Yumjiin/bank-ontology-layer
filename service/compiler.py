"""온톨로지 요청을 SQL로 변환함 (Foundry의 Object Set Service 중 쿼리 생성 부분).

LLM은 SQL을 쓰지 않고 "객체 타입 + 속성 + 사람이 쓰는 값"만 보냄.
이 모듈이 ontology.yaml 정의를 보고 원천 테이블·컬럼·코드값으로 바꿔 SQL 조건을 만듦.

구현 범위 (이슈 #3, #4)
1. 값 변환: 사람이 쓰는 값 → 원천 코드      VIP → '01', 입금 → 'IN'
2. 날짜 변환: ISO 날짜 → 원천 저장 형식      2026-07-01 → '20260701'
3. 필터 → SQL 조건                           eq, in, gte, lte, between
4. 기본 필터 자동 적용                       Transaction 조회 시 cncl_yn = 'N'
5. 객체 검색 SELECT 조립                     compile_search()
+  역변환: 조회 결과를 사람이 쓰는 값으로     '01' → VIP (policy·응답에서 사용)
6. 전체 개수                                 compile_search_count()
7. 링크 탐색                                 compile_link()       owns, has_transaction 등
8. 지표 계산                                 compile_metric()     active_customers, net_deposit
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, case, column, func, select, table
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import FromClause, Select, TableClause

from metadata import VIA_CONDITION, ObjectType, Ontology, Property
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
    apply_defaults: bool = True,
) -> Select:
    """객체 검색 요청을 SELECT 문으로 바꿈.

    - properties를 생략하면 정의된 속성 전체를 조회함
    - 결과를 식별할 수 있게 기본 키 속성은 항상 포함함
    - 결과 순서가 실행마다 바뀌지 않게 기본 키로 정렬함 (시연·평가 재현성)
    - 결과 컬럼은 원천 컬럼명 그대로이며, decode_row로 사람이 쓰는 값으로 바꿈
    - apply_defaults=False: 기본 필터를 적용하지 않음. ID로 한 건을 조회할 때 사용
      (취소된 거래도 ID로 찍어서 조회하면 보여야 하므로)
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
    requested = filters or []
    final_filters = effective_filters(ontology, obj_name, requested) if apply_defaults else list(requested)
    conditions = build_conditions(ontology, obj_name, final_filters, tbl)

    stmt = select(*columns).select_from(tbl)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    return stmt.order_by(tbl.c[obj.primary_key]).limit(limit)


def compile_search_count(
    ontology: Ontology,
    obj_name: str,
    filters: list[Filter] | None = None,
    apply_defaults: bool = True,
) -> Select:
    """compile_search와 같은 조건의 전체 개수. limit과 관계없이 "총 몇 건"인지 알려주기 위함.

    에이전트가 "여성 고객 수는?" 같은 질문에 결과를 전부 받아 세지 않고 total로 답할 수 있게 함.
    """
    obj = get_object_type(ontology, obj_name)
    tbl = to_table(obj)
    requested = filters or []
    final_filters = effective_filters(ontology, obj_name, requested) if apply_defaults else list(requested)
    conditions = build_conditions(ontology, obj_name, final_filters, tbl)
    stmt = select(func.count()).select_from(tbl)
    return stmt.where(and_(*conditions)) if conditions else stmt


# ---------------------------------------------------------------
# 7. 링크 탐색
# ---------------------------------------------------------------

def get_link(ontology: Ontology, link_name: str, from_obj: str | None = None):
    link = ontology.link_types.get(link_name)
    if link is None or (from_obj and link.from_ != from_obj):
        available = [n for n, l in ontology.link_types.items() if not from_obj or l.from_ == from_obj]
        where = f"{from_obj}에서 출발하는 " if from_obj else ""
        raise CompileError(f"{where}링크 '{link_name}'가 없음. 사용 가능: {', '.join(available) or '없음'}")
    return link


def join_link(ontology: Ontology, frm: FromClause, tables: dict[str, TableClause], link_name: str) -> FromClause:
    """현재 FROM 절에 링크 하나만큼 JOIN을 이어 붙이고, 도착 객체의 테이블을 tables에 등록함.

    via:  출발.기본키 = 관계테이블.from_key [AND 관계테이블.조건컬럼 = 값], 도착.기본키 = 관계테이블.to_key
    join: 출발.from_key = 도착.to_key
    """
    link = get_link(ontology, link_name)
    if link.to in tables:
        # 같은 테이블이 두 번 나오면 별칭이 필요함. 지금 정의에는 없는 경우라 명확히 거부함
        raise CompileError(f"링크 '{link_name}': 경로에 {link.to}가 이미 있어 다시 조인할 수 없음")
    src_obj = ontology.object_types[link.from_]
    dst_obj = ontology.object_types[link.to]
    src = tables[link.from_]
    dst = to_table(dst_obj)
    tables[link.to] = dst

    if link.via:
        via_def = link.via
        cond_col = cond_val = None
        if via_def.condition:
            m = VIA_CONDITION.match(via_def.condition)  # 형식은 기동 시 검증됨
            cond_col, cond_val = m.groups()
        cols = list(dict.fromkeys([via_def.from_key, via_def.to_key] + ([cond_col] if cond_col else [])))
        via = table(via_def.table, *(column(c) for c in cols))
        on_via = via.c[via_def.from_key] == src.c[src_obj.primary_key]
        if cond_col:
            on_via = and_(on_via, via.c[cond_col] == cond_val)
        return frm.join(via, on_via).join(dst, dst.c[dst_obj.primary_key] == via.c[via_def.to_key])

    return frm.join(dst, dst.c[link.join.to_key] == src.c[link.join.from_key])


def _link_parts(ontology: Ontology, obj_name: str, obj_id: Any, link_name: str, filters: list[Filter]):
    """링크 조회와 개수 조회가 공유하는 FROM·WHERE 절."""
    src_obj = get_object_type(ontology, obj_name)
    link = get_link(ontology, link_name, from_obj=obj_name)
    tables: dict[str, TableClause] = {obj_name: to_table(src_obj)}
    frm = join_link(ontology, tables[obj_name], tables, link_name)

    pk = primary_key_property(obj_name, src_obj)
    conditions = [build_condition(ontology, obj_name, Filter(property=pk, op="eq", value=obj_id), tables[obj_name])]
    # 도착 객체의 기본 필터도 적용함 (예: has_transaction으로 거래를 볼 때 취소 거래 제외)
    conditions += build_conditions(ontology, link.to, effective_filters(ontology, link.to, filters), tables[link.to])
    return link, tables, frm, conditions


def compile_link(
    ontology: Ontology,
    obj_name: str,
    obj_id: Any,
    link_name: str,
    filters: list[Filter] | None = None,
    properties: list[str] | None = None,
    limit: int = 20,
) -> Select:
    """기준 객체 하나에서 링크를 따라간 도착 객체들을 조회함. filters·properties는 도착 객체 기준."""
    link, tables, frm, conditions = _link_parts(ontology, obj_name, obj_id, link_name, filters or [])
    dst_obj = ontology.object_types[link.to]
    dst = tables[link.to]
    pk = primary_key_property(link.to, dst_obj)

    selected = list(dst_obj.properties) if not properties else list(dict.fromkeys(properties))
    for name in selected:
        get_property(link.to, dst_obj, name)
    if pk not in selected:
        selected.insert(0, pk)

    columns = [dst.c[dst_obj.properties[name].column] for name in selected]
    # 관계 테이블에 같은 쌍이 두 번 있어도 결과가 중복되지 않게 DISTINCT
    stmt = select(*columns).select_from(frm).where(and_(*conditions)).distinct()
    return stmt.order_by(dst.c[dst_obj.primary_key]).limit(limit)


def compile_link_count(
    ontology: Ontology, obj_name: str, obj_id: Any, link_name: str, filters: list[Filter] | None = None
) -> Select:
    link, tables, frm, conditions = _link_parts(ontology, obj_name, obj_id, link_name, filters or [])
    dst_pk = tables[link.to].c[ontology.object_types[link.to].primary_key]
    return select(func.count(dst_pk.distinct())).select_from(frm).where(and_(*conditions))


# ---------------------------------------------------------------
# 8. 지표 계산
# ---------------------------------------------------------------

def resolve_property(ontology: Ontology, objects: list[str], ref: str) -> tuple[str, str]:
    """"객체.속성" 또는 "속성"을 (객체, 속성)으로 바꿈. 객체를 생략하면 경로에서 그 속성을 가진 유일한 객체를 찾음."""
    obj_name, _, prop_name = ref.rpartition(".")
    if obj_name:
        if obj_name not in objects:
            raise CompileError(f"'{ref}': 이 지표에서 필터할 수 있는 객체는 {', '.join(objects)}")
        get_property(obj_name, ontology.object_types[obj_name], prop_name)
        return obj_name, prop_name
    owners = [o for o in objects if prop_name in ontology.object_types[o].properties]
    if len(owners) == 1:
        return owners[0], prop_name
    if not owners:
        allowed = [f"{o}.{p}" for o in objects for p in ontology.object_types[o].properties]
        raise CompileError(f"속성 '{prop_name}'을 이 지표의 객체에서 찾을 수 없음. 사용 가능: {', '.join(allowed)}")
    raise CompileError(f"속성 '{prop_name}'이 {', '.join(owners)}에 모두 있음. '{owners[-1]}.{prop_name}'처럼 객체를 붙여 지정")


def compile_metric(
    ontology: Ontology,
    name: str,
    start: str,
    end: str,
    filters: list[Filter] | None = None,
) -> Select:
    """지표 정의(ontology.yaml metrics)를 집계 SELECT로 바꿈. 결과는 value 한 칸.

    조건 = 정의에 고정된 filters + 기간 + 요청 필터 + 경로상 객체의 기본 필터
    요청 필터는 정의를 덮어쓰지 못함 (예: active_customers에서 type을 이자로 바꿀 수 없음).
    기본 필터는 정의나 요청이 같은 속성을 지정하면 빠짐 (일반 검색과 같은 규칙).
    """
    metric = ontology.metrics.get(name)
    if metric is None:
        raise CompileError(f"존재하지 않는 지표 '{name}'. 사용 가능: {', '.join(ontology.metrics) or '없음'}")
    objects = ontology.path_objects(metric)

    tables: dict[str, TableClause] = {metric.object: to_table(ontology.object_types[metric.object])}
    frm: FromClause = tables[metric.object]
    for step in metric.path:
        frm = join_link(ontology, frm, tables, step)

    # 객체별로 필터를 모은 뒤, 객체마다 기본 필터를 합침
    per_object: dict[str, list[Filter]] = {o: [] for o in objects}

    def add(ref: str, op: str, value: Any) -> None:
        o, prop = resolve_property(ontology, objects, ref)
        per_object[o].append(Filter(property=prop, op=op, value=value))

    for f in metric.filters:
        add(f.property, f.op, f.value)
    add(metric.period, "between", [start, end])

    fixed = {resolve_property(ontology, objects, f.property) for f in metric.filters}
    for f in filters or []:
        o, prop = resolve_property(ontology, objects, f.property)
        if (o, prop) in fixed:
            raise CompileError(f"{name}: {o}.{prop}는 지표 정의에 고정된 조건이라 요청에서 바꿀 수 없음")
        per_object[o].append(Filter(property=prop, op=f.op, value=f.value))

    conditions = []
    for o, fs in per_object.items():
        conditions += build_conditions(ontology, o, effective_filters(ontology, o, fs), tables[o])

    measure = metric.measure
    if measure.agg == "count":
        expr = func.count()
    else:
        mo, mp = resolve_property(ontology, objects, measure.property)
        col = tables[mo].c[ontology.object_types[mo].properties[mp].column]
        if measure.agg == "count_distinct":
            expr = func.count(col.distinct())
        else:
            if measure.sign:
                so, sp = resolve_property(ontology, objects, measure.sign.property)
                sprop = ontology.object_types[so].properties[sp]
                codes = [encode_value(so, sp, sprop, v) for v in measure.sign.negative]
                scol = tables[so].c[sprop.column]
                col = case((scol.in_(codes), -col), else_=col)
            # 해당 기간에 거래가 없으면 SUM은 NULL이므로 0으로 바꿈
            expr = func.coalesce(func.sum(col), 0)

    return select(expr.label("value")).select_from(frm).where(and_(*conditions))
