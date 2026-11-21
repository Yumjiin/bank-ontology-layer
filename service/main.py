"""Ontology Service (FastAPI).

현재 상태: /health, /ontology 동작. 나머지 엔드포인트는 계약(요청 형식)만 정의하고 501을 반환함.
응답 형식은 docs/fixtures/의 예시 JSON을 따름.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request

from compiler import check_default_filters
from metadata import OntologyError, load_ontology
from schemas import ActionRequest, LinkRequest, MetricRequest, SearchRequest

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
    yield


app = FastAPI(title="Bank Ontology Service", version="0.1.0", lifespan=lifespan)


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
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    not_implemented("search_objects")


@app.get("/objects/{object_type}/{object_id}")
def get_object(
    object_type: str,
    object_id: str,
    x_role: RoleHeader = Header(...),
    x_session_id: str = Header(...),
):
    not_implemented("get_object")


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


@app.get("/traces")
def list_traces(session_id: str):
    not_implemented("list_traces")