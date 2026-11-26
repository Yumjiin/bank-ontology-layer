"""액션 요청·승인·거절·실행 (Foundry의 Actions 역할).

데이터를 바꾸는 행동은 LLM이 바로 실행하지 못하고, 다음 단계를 거침.

1. 요청   allowed_roles에 속한 역할만 가능 → action_request에 PENDING으로 기록 (대상 데이터는 그대로)
2. 승인   approver_roles에 속한 역할만 가능 → 실행 직전에 대상 상태를 다시 확인하고 effect 적용 → EXECUTED
   거절   → REJECTED (대상 데이터 변화 없음)

보호 규칙 (409 CONFLICT)
- 같은 대상에 같은 액션의 승인 대기 요청이 이미 있음
- 대상의 현재 상태가 effect.allowed_from에 없음 (예: 해지됐거나 이미 지급정지된 계좌)
- 이미 처리된 요청을 다시 승인·거절함

범위 밖: 사용자 인증이 없어 "요청자와 승인자가 다른 사람"(4-eyes)은 검사하지 않음. 역할 기준으로만 통제함.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, column, func, insert, select, table, update
from sqlalchemy.engine import Connection, Engine

import policy
import query
from compiler import (
    CompileError,
    compile_search,
    decode_row,
    encode_value,
    get_object_type,
    primary_key_property,
    to_table,
)
from metadata import ActionType, Ontology
from schemas import ActionRequest, Filter

STATUSES = ("PENDING", "APPROVED", "REJECTED", "EXECUTED")

ACTION_REQUEST = table(
    "action_request",
    column("request_id"),
    column("action_type"),
    column("target_type"),
    column("target_id"),
    column("params", JSON),
    column("requested_role"),
    column("session_id"),
    column("status"),
    column("created_at"),
    column("decided_at"),
)


class ConflictError(Exception):
    """현재 상태에서 할 수 없는 요청. API에서는 409(CONFLICT)로 반환함."""


# ---------------------------------------------------------------
# 공통
# ---------------------------------------------------------------

def get_action(ontology: Ontology, name: str) -> ActionType:
    action = ontology.action_types.get(name)
    if action is None:
        raise CompileError(f"존재하지 않는 액션 '{name}'. 사용 가능: {', '.join(ontology.action_types) or '없음'}")
    return action


def _require_role(roles: list[str], role: str, what: str) -> None:
    if role not in roles:
        raise policy.PolicyError(f"{role} 역할은 {what}할 수 없음. 허용 역할: {', '.join(roles)}", role)


def _validate_params(name: str, action: ActionType, params: dict[str, Any]) -> dict[str, str]:
    missing = [k for k in action.params if not str(params.get(k) or "").strip()]
    if missing:
        raise CompileError(f"{name}: 필수 파라미터 누락 {', '.join(missing)}")
    unknown = [k for k in params if k not in action.params]
    if unknown:
        raise CompileError(f"{name}: 정의되지 않은 파라미터 {', '.join(unknown)}. 사용 가능: {', '.join(action.params)}")
    return {k: str(params[k]).strip() for k in action.params}


def _current_value(conn: Connection, ontology: Ontology, action: ActionType, target_id: str) -> Any:
    """대상 객체의 effect 속성 현재 값 (사람이 쓰는 값). 대상이 없으면 NotFoundError."""
    obj = get_object_type(ontology, action.target)
    pk = primary_key_property(action.target, obj)
    stmt = compile_search(
        ontology,
        action.target,
        [Filter(property=pk, op="eq", value=target_id)],
        properties=[action.effect.property],
        limit=1,
        apply_defaults=False,
    )
    row = conn.execute(stmt).mappings().first()
    if row is None:
        raise query.NotFoundError(f"{action.target} '{target_id}'를 찾을 수 없음")
    return decode_row(obj, dict(row))[action.effect.property]


def _check_allowed_from(name: str, action: ActionType, target_id: str, current: Any) -> None:
    if action.effect.allowed_from and current not in action.effect.allowed_from:
        raise ConflictError(
            f"{name}: {action.target} '{target_id}'의 현재 {action.effect.property}가 '{current}'라 실행할 수 없음 "
            f"(가능한 상태: {', '.join(action.effect.allowed_from)})"
        )


def _execute(conn: Connection, ontology: Ontology, action: ActionType, target_id: str) -> dict[str, Any]:
    """effect를 대상에 적용함. 값은 encode_value로 원천 코드로 바꿔서 바인드 파라미터로 씀."""
    obj = get_object_type(ontology, action.target)
    pk = primary_key_property(action.target, obj)
    prop = obj.properties[action.effect.property]
    tbl = to_table(obj)
    key = encode_value(action.target, pk, obj.properties[pk], target_id)
    code = encode_value(action.target, action.effect.property, prop, action.effect.value)
    conn.execute(update(tbl).where(tbl.c[obj.primary_key] == key).values({prop.column: code}))
    return {pk: target_id, action.effect.property: action.effect.value}


def _ts(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _request_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    return {
        "request_id": d["request_id"],
        "action_type": d["action_type"],
        "target_type": d["target_type"],
        "target_id": d["target_id"],
        "params": d["params"],
        "requested_role": d["requested_role"],
        "status": d["status"],
        "created_at": _ts(d["created_at"]),
        "decided_at": _ts(d["decided_at"]),
    }


# ---------------------------------------------------------------
# 요청
# ---------------------------------------------------------------

def request_action(
    engine: Engine, ontology: Ontology, name: str, req: ActionRequest, *, role: str, session_id: str
) -> dict[str, Any]:
    """POST /actions/{name} 처리. requires_approval이면 승인 대기로 만들고, 아니면 바로 실행함."""
    action = get_action(ontology, name)
    _require_role(action.allowed_roles, role, f"{name}를 요청")
    params = _validate_params(name, action, req.params)

    with engine.begin() as conn:
        current = _current_value(conn, ontology, action, req.target_id)
        _check_allowed_from(name, action, req.target_id, current)

        pending = conn.execute(
            select(ACTION_REQUEST.c.request_id).where(
                ACTION_REQUEST.c.action_type == name,
                ACTION_REQUEST.c.target_id == req.target_id,
                ACTION_REQUEST.c.status == "PENDING",
            )
        ).first()
        if pending:
            raise ConflictError(f"{name}: {action.target} '{req.target_id}'에 승인 대기 중인 요청 #{pending[0]}이 이미 있음")

        values = {
            "action_type": name,
            "target_type": action.target,
            "target_id": req.target_id,
            "params": params,
            "requested_role": role,
            "session_id": session_id,
            "status": "PENDING",
        }
        if not action.requires_approval:
            values.update(status="EXECUTED", decided_at=func.now())
        request_id = conn.execute(insert(ACTION_REQUEST).values(values).returning(ACTION_REQUEST.c.request_id)).scalar_one()

        result = _execute(conn, ontology, action, req.target_id) if not action.requires_approval else None

    response = {
        "request_id": request_id,
        "action_type": name,
        "target_type": action.target,
        "target_id": req.target_id,
        "params": params,
        "status": values["status"],
        "message": (
            "승인 요청이 생성되었습니다. 관리자 승인 후 실행됩니다."
            if action.requires_approval
            else "실행되었습니다."
        ),
        "trace_id": query.new_trace_id(),
    }
    if result is not None:
        response["result"] = result
    return response


# ---------------------------------------------------------------
# 승인 대기 목록, 승인, 거절
# ---------------------------------------------------------------

def _approver_roles(ontology: Ontology) -> list[str]:
    return sorted({r for a in ontology.action_types.values() for r in a.approvers})


def list_approvals(engine: Engine, ontology: Ontology, status: str, *, role: str) -> dict[str, Any]:
    """GET /approvals 처리. 승인 권한이 있는 역할만 볼 수 있음."""
    _require_role(_approver_roles(ontology), role, "승인 목록을 조회")
    if status != "ALL" and status not in STATUSES:
        raise CompileError(f"status는 {', '.join(STATUSES)}, ALL 중 하나여야 함 (받은 값: {status})")

    stmt = select(ACTION_REQUEST).order_by(ACTION_REQUEST.c.request_id)
    if status != "ALL":
        stmt = stmt.where(ACTION_REQUEST.c.status == status)
    with engine.connect() as conn:
        items = [_request_dict(r) for r in conn.execute(stmt).mappings()]
    return {"status": status, "count": len(items), "items": items}


def _load_request(conn: Connection, request_id: int) -> dict[str, Any]:
    row = conn.execute(select(ACTION_REQUEST).where(ACTION_REQUEST.c.request_id == request_id)).mappings().first()
    if row is None:
        raise query.NotFoundError(f"승인 요청 #{request_id}를 찾을 수 없음")
    return dict(row)


def _decide(conn: Connection, request_id: int, new_status: str) -> None:
    """PENDING인 경우에만 상태를 바꿈. 동시에 두 번 승인돼도 한 번만 성공하도록 조건부 UPDATE를 씀."""
    done = conn.execute(
        update(ACTION_REQUEST)
        .where(ACTION_REQUEST.c.request_id == request_id, ACTION_REQUEST.c.status == "PENDING")
        .values(status=new_status, decided_at=func.now())
    )
    if done.rowcount == 0:
        raise ConflictError(f"승인 요청 #{request_id}는 이미 처리됨")


def approve(engine: Engine, ontology: Ontology, request_id: int, *, role: str) -> dict[str, Any]:
    """POST /approvals/{id}/approve 처리. 상태 변경과 실행을 한 트랜잭션으로 묶어, 실행이 실패하면 승인도 취소됨."""
    with engine.begin() as conn:
        req = _load_request(conn, request_id)
        action = get_action(ontology, req["action_type"])
        _require_role(action.approvers, role, f"{req['action_type']}를 승인")
        if req["status"] != "PENDING":
            raise ConflictError(f"승인 요청 #{request_id}는 이미 {req['status']} 상태")

        _decide(conn, request_id, "EXECUTED")
        # 요청 이후 대상 상태가 바뀌었을 수 있으므로 실행 직전에 다시 확인함
        before = _current_value(conn, ontology, action, req["target_id"])
        _check_allowed_from(req["action_type"], action, req["target_id"], before)
        result = _execute(conn, ontology, action, req["target_id"])

    return {
        "request_id": request_id,
        "status": "EXECUTED",
        "result": result,
        "change": {"property": action.effect.property, "from": before, "to": action.effect.value},
        "trace_id": query.new_trace_id(),
    }


def reject(engine: Engine, ontology: Ontology, request_id: int, *, role: str) -> dict[str, Any]:
    """POST /approvals/{id}/reject 처리. 대상 데이터는 바뀌지 않음."""
    with engine.begin() as conn:
        req = _load_request(conn, request_id)
        action = get_action(ontology, req["action_type"])
        _require_role(action.approvers, role, f"{req['action_type']}를 거절")
        if req["status"] != "PENDING":
            raise ConflictError(f"승인 요청 #{request_id}는 이미 {req['status']} 상태")
        _decide(conn, request_id, "REJECTED")

    return {"request_id": request_id, "status": "REJECTED", "trace_id": query.new_trace_id()}
