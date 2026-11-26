"""액션 요청·승인·거절 테스트. 데이터를 바꾸므로 테스트마다 새 메모리 DB를 씀."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import actions
import policy
import query
from compiler import CompileError
from schemas import ActionRequest
from test_query import HEADERS, SEED_SQL, onto  # noqa: F401  (onto fixture 재사용)

ACTION_SQL = """
INSERT INTO acct VALUES ('A03', 'C');
CREATE TABLE action_request (
  request_id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_type TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}', requested_role TEXT NOT NULL, session_id TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, decided_at TEXT
);
"""
REASON = {"reason": "보이스피싱 의심"}


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as conn:
        for stmt in (SEED_SQL + ACTION_SQL).split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    return eng


def status_of(engine, acct_no):
    with engine.connect() as conn:
        return conn.execute(text("SELECT acct_stat_cd FROM acct WHERE acct_no = :a"), {"a": acct_no}).scalar_one()


def request(engine, onto, target="A01", role="manager", params=REASON):
    return actions.request_action(engine, onto, "freeze_account", ActionRequest(target_id=target, params=params),
                                  role=role, session_id="s1")


# ---------------------------------------------------------------
# 요청
# ---------------------------------------------------------------

def test_teller_cannot_request(engine, onto):
    with pytest.raises(policy.PolicyError, match="허용 역할: manager"):
        request(engine, onto, role="teller")


def test_manager_request_creates_pending_without_changing_data(engine, onto):
    res = request(engine, onto)
    assert res["status"] == "PENDING" and res["params"] == REASON
    assert status_of(engine, "A01") == "A"  # 승인 전에는 그대로


def test_duplicate_pending_request_conflicts(engine, onto):
    first = request(engine, onto)
    with pytest.raises(actions.ConflictError, match=f"#{first['request_id']}"):
        request(engine, onto)


def test_closed_account_cannot_be_frozen(engine, onto):
    with pytest.raises(actions.ConflictError, match="해지"):
        request(engine, onto, target="A03")


@pytest.mark.parametrize("params,match", [({}, "누락"), ({"reason": "  "}, "누락"), ({**REASON, "x": 1}, "정의되지 않은")])
def test_params_validation(engine, onto, params, match):
    with pytest.raises(CompileError, match=match):
        request(engine, onto, params=params)


def test_unknown_target_and_action(engine, onto):
    with pytest.raises(query.NotFoundError):
        request(engine, onto, target="A99")
    with pytest.raises(CompileError, match="freeze_account"):
        actions.request_action(engine, onto, "close_account", ActionRequest(target_id="A01"), role="manager", session_id="s")


# ---------------------------------------------------------------
# 승인·거절
# ---------------------------------------------------------------

def test_approve_executes(engine, onto):
    rid = request(engine, onto)["request_id"]
    res = actions.approve(engine, onto, rid, role="manager")
    assert res["status"] == "EXECUTED"
    assert res["change"] == {"property": "status", "from": "활성", "to": "지급정지"}
    assert status_of(engine, "A01") == "F"
    with pytest.raises(actions.ConflictError, match="이미"):
        actions.approve(engine, onto, rid, role="manager")  # 두 번 승인 불가


def test_teller_cannot_approve(engine, onto):
    rid = request(engine, onto)["request_id"]
    with pytest.raises(policy.PolicyError):
        actions.approve(engine, onto, rid, role="teller")
    assert status_of(engine, "A01") == "A"


def test_reject_keeps_data(engine, onto):
    rid = request(engine, onto)["request_id"]
    assert actions.reject(engine, onto, rid, role="manager")["status"] == "REJECTED"
    assert status_of(engine, "A01") == "A"
    request(engine, onto)  # 거절된 뒤에는 다시 요청할 수 있음


def test_state_rechecked_at_approval(engine, onto):
    # 요청 후 다른 경로로 계좌가 해지되면 승인 시점에 거부하고, 요청 상태도 PENDING으로 되돌림 (트랜잭션 롤백)
    rid = request(engine, onto)["request_id"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE acct SET acct_stat_cd = 'C' WHERE acct_no = 'A01'"))
    with pytest.raises(actions.ConflictError, match="해지"):
        actions.approve(engine, onto, rid, role="manager")
    assert actions.list_approvals(engine, onto, "PENDING", role="manager")["items"][0]["request_id"] == rid


def test_list_approvals(engine, onto):
    request(engine, onto)
    request(engine, onto, target="A02")
    res = actions.list_approvals(engine, onto, "PENDING", role="manager")
    assert [i["target_id"] for i in res["items"]] == ["A01", "A02"]
    assert res["items"][0]["params"] == REASON and res["items"][0]["requested_role"] == "manager"
    with pytest.raises(policy.PolicyError):
        actions.list_approvals(engine, onto, "PENDING", role="teller")
    with pytest.raises(CompileError):
        actions.list_approvals(engine, onto, "DONE", role="manager")


def test_missing_request(engine, onto):
    with pytest.raises(query.NotFoundError):
        actions.approve(engine, onto, 999, role="manager")


# ---------------------------------------------------------------
# API (시연 시나리오 5·6번 흐름)
# ---------------------------------------------------------------

def test_api_flow(engine, onto):
    from main import app

    teller = {**HEADERS, "X-Role": "teller"}
    manager = {**HEADERS, "X-Role": "manager"}
    body = {"target_id": "A01", "params": REASON}
    with TestClient(app) as c:
        app.state.engine, app.state.ontology = engine, onto

        res = c.post("/actions/freeze_account", headers=teller, json=body)
        assert res.status_code == 403 and res.json()["detail"]["code"] == "FORBIDDEN_ROLE"

        res = c.post("/actions/freeze_account", headers=manager, json=body)
        assert res.status_code == 200 and res.json()["status"] == "PENDING"
        rid = res.json()["request_id"]

        assert c.post("/actions/freeze_account", headers=manager, json=body).status_code == 409
        assert c.get("/approvals", headers=manager).json()["count"] == 1

        res = c.post(f"/approvals/{rid}/approve", headers=manager)
        assert res.status_code == 200 and res.json()["result"]["status"] == "지급정지"
        assert c.get("/objects/Account/A01", headers=teller).json()["item"]["status"] == "지급정지"
        assert c.post(f"/approvals/{rid}/approve", headers=manager).status_code == 409
