"""Ontology Service (FastAPI).

온톨로지, 객체 조회, 에이전트 실행 기록 API를 제공함.
응답 형식은 docs/fixtures/의 예시 JSON을 따름.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import JSON, bindparam, text
from sqlalchemy.exc import OperationalError

import query
from compiler import CompileError, check_default_filters
from metadata import OntologyError, load_ontology
from schemas import ActionRequest, LinkRequest, MetricRequest, SearchRequest, TraceStep

ONTOLOGY_PATH = os.environ.get("ONTOLOGY_PATH", "/app/ontology/ontology.yaml")
RoleHeader = Literal["teller", "marketer", "manager"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 정의에 오류가 있으면 여기서 예외가 나며 서비스가 뜨지 않음 (설계서 4-3)
    ontology = load_ontology(ONTOLOGY_PATH)
    errors = check_default_filters(ontology)
    if errors:
        raise OntologyError("default_filters 검증 실패:\n- " + "\n- ".join(errors))
    app.state.ontology = ontology
    engine = query.make_engine()
    app.state.engine = engine
    yield
    engine.dispose()  # app.state.engine이 바뀌었더라도(테스트 등) 직접 만든 엔진만 정리함


app = FastAPI(title="Bank Ontology Service", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------
# 오류 응답
# 에이전트가 읽고 다시 시도할 수 있게 code와 message를 함께 돌려줌 (docs/api-contract.md 오류 응답)
# ---------------------------------------------------------------

@app.exception_handler(CompileError)
async def handle_compile_error(request: Request, exc: CompileError):
    return JSONResponse(status_code=400, content={"detail": {"code": "INVALID_REQUEST", "message": str(exc)}})


@app.exception_handler(query.NotFoundError)
async def handle_not_found(request: Request, exc: query.NotFoundError):
    return JSONResponse(status_code=404, content={"detail": {"code": "NOT_FOUND", "message": str(exc)}})


@app.exception_handler(OperationalError)
async def handle_db_unavailable(request: Request, exc: OperationalError):
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "DB_UNAVAILABLE", "message": "DB에 연결할 수 없음. db 컨테이너와 DATABASE_URL을 확인"}},
    )


def not_implemented(name: str):
    raise HTTPException(status_code=501, detail=f"{name}: 아직 구현되지 않음. 응답 형식은 docs/fixtures 참고")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ontology")
def get_ontology(request: Request):
    return request.app.state.ontology.summary()


@app.post("/objects/search")
def search_objects(
    body: SearchRequest,
    request: Request,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    return query.search_objects(request.app.state.engine, request.app.state.ontology, body)


@app.get("/objects/{object_type}/{object_id}")
def get_object(
    object_type: str,
    object_id: str,
    request: Request,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    return query.get_object(request.app.state.engine, request.app.state.ontology, object_type, object_id)


@app.post("/objects/{object_type}/{object_id}/links/{link}")
def get_linked_objects(
    object_type: str,
    object_id: str,
    link: str,
    body: LinkRequest,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    not_implemented("get_linked_objects")


@app.post("/metrics/{name}")
def get_metric(
    name: str,
    body: MetricRequest,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    not_implemented("get_metric")


@app.post("/actions/{action_type}")
def request_action(
    action_type: str,
    body: ActionRequest,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    not_implemented("request_action")


@app.get("/approvals")
def list_approvals(status: str = "PENDING", x_role: RoleHeader = Header(...)):
    not_implemented("list_approvals")


@app.post("/approvals/{request_id}/approve")
def approve(request_id: int, x_role: RoleHeader = Header(...)):
    not_implemented("approve")


@app.post("/approvals/{request_id}/reject")
def reject(request_id: int, x_role: RoleHeader = Header(...)):
    not_implemented("reject")


@app.post("/traces", status_code=201)
def save_trace_step(
    body: TraceStep,
    request: Request,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    stmt = text("""
        INSERT INTO agent_trace (
            trace_id, session_id, step, span_type, tool_name, input,
            output_summary, sql_text, policy_decision, latency_ms
        ) VALUES (
            :trace_id, :session_id, :step, :span_type, :tool_name, :input,
            :output_summary, :sql_text, :policy_decision, :latency_ms
        )
    """).bindparams(bindparam("input", type_=JSON(none_as_null=True)))
    with request.app.state.engine.begin() as conn:
        conn.execute(stmt, {**body.model_dump(), "session_id": x_session_id})
    return {"trace_id": body.trace_id, "step": body.step}


@app.get("/traces")
def list_traces(
    session_id: str,
    request: Request,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    if session_id != x_session_id:
        raise HTTPException(status_code=403, detail={
            "code": "FORBIDDEN_SESSION",
            "message": "현재 세션의 실행 기록만 조회할 수 있습니다.",
        })
    stmt = text("""
        SELECT trace_id, step, span_type, tool_name, input,
               output_summary, sql_text, policy_decision, latency_ms
        FROM agent_trace
        WHERE session_id = :session_id
        ORDER BY id
    """).columns(input=JSON)
    with request.app.state.engine.connect() as conn:
        items = [dict(row) for row in conn.execute(stmt, {"session_id": session_id}).mappings()]
    return {"session_id": session_id, "items": items}
