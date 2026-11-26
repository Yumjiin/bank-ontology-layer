"""역할별 마스킹·차단 테스트. test_query의 SQLite 데이터와 테스트 온톨로지를 그대로 사용함."""

import pytest

import policy
import query
from schemas import Filter, LinkRequest, MetricRequest, Period, SearchRequest
from test_query import HEADERS, client, engine, onto  # noqa: F401  (fixture 재사용)

VIP = [Filter(property="grade", op="eq", value="VIP")]


# ---------------------------------------------------------------
# policy 모듈 단위
# ---------------------------------------------------------------

def test_restricted_properties(onto):
    assert policy.restricted_properties(onto, "Customer", "teller") == (set(), set())
    assert policy.restricted_properties(onto, "Customer", "marketer") == ({"name"}, set())
    assert policy.restricted_properties(onto, "Customer", "auditor") == (set(), {"name"})
    assert policy.restricted_properties(onto, "Account", "marketer") == (set(), set())  # PII 속성이 없는 객체


def test_apply_masks_and_blocks(onto):
    items = [{"cust_no": "C01", "name": "김하나", "grade": "VIP"}, {"cust_no": "C02", "name": None, "grade": "일반"}]
    masked, p = policy.apply_to_items(onto, "Customer", "marketer", items)
    assert masked[0] == {"cust_no": "C01", "name": "***", "grade": "VIP"}
    assert masked[1]["name"] is None  # 없는 값은 그대로 둠
    assert p == {"masked": ["name"], "blocked": []}

    blocked, p = policy.apply_to_items(onto, "Customer", "auditor", items)
    assert "name" not in blocked[0] and p == {"masked": [], "blocked": ["name"]}


def test_policy_lists_only_returned_properties(onto):
    # name을 요청하지 않았으면 policy에도 기록하지 않음
    _, p = policy.apply_to_items(onto, "Customer", "marketer", [{"cust_no": "C01", "grade": "VIP"}])
    assert p == {"masked": [], "blocked": []}


def test_unknown_role(onto):
    with pytest.raises(policy.PolicyError):
        policy.restricted_properties(onto, "Customer", "intern")


# ---------------------------------------------------------------
# 조회 경로별 적용
# ---------------------------------------------------------------

def test_search_by_role(engine, onto):
    req = SearchRequest(object_type="Customer", filters=VIP)
    teller = query.search_objects(engine, onto, req, role="teller")
    marketer = query.search_objects(engine, onto, req, role="marketer")
    assert [i["name"] for i in teller["items"]] == ["김하나", "박세나"]
    assert [i["name"] for i in marketer["items"]] == ["***", "***"]
    assert marketer["policy"] == {"masked": ["name"], "blocked": []}
    assert marketer["total"] == teller["total"] == 2  # 개수는 역할과 무관


def test_get_object_masked(engine, onto):
    res = query.get_object(engine, onto, "Customer", "C01", role="marketer")
    assert res["item"]["name"] == "***" and res["policy"]["masked"] == ["name"]


def test_blocked_property_removed_even_if_requested(engine, onto):
    req = SearchRequest(object_type="Customer", properties=["name", "grade"], limit=1)
    res = query.search_objects(engine, onto, req, role="auditor")
    assert res["items"] == [{"cust_no": "C01", "grade": "VIP"}] and res["policy"]["blocked"] == ["name"]


def test_filter_on_masked_property_is_forbidden(engine, onto):
    # 이름을 못 보는 역할이 이름으로 검색하면 결과 유무로 정보를 알아낼 수 있음
    req = SearchRequest(object_type="Customer", filters=[Filter(property="name", op="eq", value="김하나")])
    with pytest.raises(policy.PolicyError, match="검색할 수 없음"):
        query.search_objects(engine, onto, req, role="marketer")
    assert query.search_objects(engine, onto, req, role="teller")["total"] == 1


def test_metric_filter_on_masked_property_is_forbidden(engine, onto):
    req = MetricRequest(period=Period(start="2026-09-01", end="2026-09-30"),
                        filters=[Filter(property="Customer.name", op="eq", value="김하나")])
    with pytest.raises(policy.PolicyError):
        query.get_metric(engine, onto, "active_customers", req, role="marketer")
    assert query.get_metric(engine, onto, "active_customers", req, role="teller")["value"] == 1


def test_metric_value_not_masked(engine, onto):
    req = MetricRequest(period=Period(start="2026-09-01", end="2026-09-30"))
    assert query.get_metric(engine, onto, "net_deposit", req, role="marketer")["value"] == 80000


def test_link_source_check_respects_role(engine, onto):
    # 출발 객체 확인은 역할과 무관하게 동작하고, 도착 객체(Account)에는 PII가 없어 그대로
    res = query.get_linked_objects(engine, onto, "Customer", "C01", "owns", LinkRequest(), role="marketer")
    assert res["items"] == [{"acct_no": "A01", "status": "활성"}] and res["policy"] == {"masked": [], "blocked": []}


# ---------------------------------------------------------------
# API
# ---------------------------------------------------------------

def test_api_masking_and_403(client):
    marketer = {**HEADERS, "X-Role": "marketer"}
    body = {"object_type": "Customer", "filters": [{"property": "grade", "op": "eq", "value": "VIP"}]}
    res = client.post("/objects/search", headers=marketer, json=body)
    assert res.status_code == 200 and res.json()["items"][0]["name"] == "***"

    body = {"object_type": "Customer", "filters": [{"property": "name", "op": "eq", "value": "김하나"}]}
    res = client.post("/objects/search", headers=marketer, json=body)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "FORBIDDEN_ROLE" and res.json()["detail"]["role"] == "marketer"
