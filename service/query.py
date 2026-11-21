"""객체 조회 실행 (Foundry의 Object Set Service 중 실행 부분).

compiler가 만든 SELECT를 DB에서 실행하고, 결과를 사람이 쓰는 값으로 바꿔
API 응답 형식(docs/fixtures/objects_search.json)으로 돌려줌.

역할별 마스킹(policy)은 이슈 #5에서 이 결과에 적용함. 지금은 policy 필드에 빈 값만 채움.
실행 기록 저장(agent_trace)은 이슈 #17에서 연결함. 지금은 trace_id만 발급함.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.sql.expression import Select

from compiler import compile_search, decode_row, get_object_type, primary_key_property
from metadata import Ontology
from schemas import Filter, SearchRequest

DEFAULT_DATABASE_URL = "postgresql://bank:bank@localhost:5433/bank"


class NotFoundError(LookupError):
    """요청한 ID의 객체가 없을 때 발생함. API에서는 404로 반환함."""


# ---------------------------------------------------------------
# DB 연결
# ---------------------------------------------------------------

def database_url() -> str:
    """환경변수의 DATABASE_URL을 SQLAlchemy + psycopg 3 형식으로 맞춤.

    .env에는 postgresql://로 적혀 있어 seed(psycopg 직접 사용)와 같은 값을 공유하고,
    SQLAlchemy에서 쓸 때만 드라이버를 붙임.
    """
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def make_engine(url: str | None = None) -> Engine:
    # create_engine은 실제로 쿼리를 실행할 때 연결하므로, DB 없이도 서비스와 테스트가 뜰 수 있음
    return create_engine(url or database_url(), pool_pre_ping=True)


# ---------------------------------------------------------------
# 공통
# ---------------------------------------------------------------

def new_trace_id() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


def render_sql(stmt: Select, engine: Engine) -> str:
    """응답·실행 기록에 보여줄 SQL. 값이 채워진 형태로 바꾸지만 실행에는 쓰지 않음 (실행은 바인드 파라미터)."""
    return str(stmt.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))


def empty_policy() -> dict[str, list[str]]:
    return {"masked": [], "blocked": []}  # TODO(#5): 역할별 마스킹 결과로 채움


# ---------------------------------------------------------------
# 객체 검색
# ---------------------------------------------------------------

def search_objects(engine: Engine, ontology: Ontology, req: SearchRequest) -> dict[str, Any]:
    """POST /objects/search 처리.

    limit보다 1건 더 조회해 결과가 잘렸는지(has_more) 알려줌.
    에이전트가 "20명"을 전체 결과로 오해하지 않고, 필터를 좁히거나 지표(get_metric)를 쓰도록 판단할 수 있게 하기 위함.
    """
    obj = get_object_type(ontology, req.object_type)
    stmt = compile_search(ontology, req.object_type, req.filters, req.properties, limit=req.limit + 1)

    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt).mappings()]

    has_more = len(rows) > req.limit
    items = [decode_row(obj, row) for row in rows[: req.limit]]

    return {
        "object_type": req.object_type,
        "count": len(items),
        "has_more": has_more,
        "items": items,
        "policy": empty_policy(),
        "sql": render_sql(compile_search(ontology, req.object_type, req.filters, req.properties, limit=req.limit), engine),
        "trace_id": new_trace_id(),
    }


def get_object(engine: Engine, ontology: Ontology, object_type: str, object_id: str) -> dict[str, Any]:
    """GET /objects/{type}/{id} 처리. 기본 필터 없이 기본 키로 한 건을 조회함."""
    obj = get_object_type(ontology, object_type)
    pk = primary_key_property(object_type, obj)
    stmt = compile_search(
        ontology,
        object_type,
        [Filter(property=pk, op="eq", value=object_id)],
        limit=1,
        apply_defaults=False,
    )

    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()

    if row is None:
        raise NotFoundError(f"{object_type} '{object_id}'를 찾을 수 없음")

    return {
        "object_type": object_type,
        "item": decode_row(obj, dict(row)),
        "policy": empty_policy(),
        "sql": render_sql(stmt, engine),
        "trace_id": new_trace_id(),
    }
