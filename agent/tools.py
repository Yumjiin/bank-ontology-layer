"""OpenAI function calling에 제공하는 온톨로지 도구 정의."""

import json
import os
from pathlib import Path

import requests

FIXTURES = Path(__file__).resolve().parents[1] / "docs" / "fixtures"
TOOL_FIXTURES = {
    "search_objects": "objects_search.json",
    "get_linked_objects": "linked_objects.json",
    "get_metric": "metric.json",
}

FILTERS = {
    "type": "array",
    "description": "서비스가 해석할 필터. 값은 VIP, 입금, YYYY-MM-DD 등 업무 표현으로 보낸다.",
    "items": {
        "type": "object",
        "properties": {
            "property": {"type": "string"},
            "op": {"type": "string", "enum": ["eq", "in", "gte", "lte", "between"]},
            "value": {
                "description": "in은 배열, between은 양 끝 값의 배열, 나머지는 단일 값.",
                "type": ["string", "number", "boolean", "array", "null"],
                "items": {"type": ["string", "number", "boolean", "null"]},
            },
        },
        "required": ["property", "op", "value"],
        "additionalProperties": False,
    },
}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "strict": False,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    _function("describe_ontology", "사용 가능한 객체, 속성, 링크, 지표, 액션을 확인한다.", {}, []),
    _function(
        "search_objects",
        "필터에 맞는 온톨로지 객체를 검색한다.",
        {
            "object_type": {"type": "string", "description": "예: Customer"},
            "filters": FILTERS,
            "properties": {"type": "array", "items": {"type": "string"}},
            "limit": LIMIT,
        },
        ["object_type"],
    ),
    _function(
        "get_linked_objects",
        "기준 객체의 링크를 따라 연결된 객체를 조회한다.",
        {
            "object_type": {"type": "string"},
            "object_id": {"type": "string"},
            "link": {"type": "string", "description": "온톨로지에 정의된 링크 이름"},
            "filters": FILTERS,
            "limit": LIMIT,
        },
        ["object_type", "object_id", "link"],
    ),
    _function(
        "get_metric",
        "지정 기간의 온톨로지 지표를 계산한다. 집계 질문에 우선 사용한다.",
        {
            "name": {"type": "string", "description": "예: active_customers, net_deposit"},
            "period": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "시작일 (YYYY-MM-DD)"},
                    "end": {"type": "string", "description": "종료일 (YYYY-MM-DD)"},
                },
                "required": ["start", "end"],
                "additionalProperties": False,
            },
            "filters": FILTERS,
        },
        ["name", "period"],
    ),
    _function(
        "request_action",
        "온톨로지 액션의 승인 요청을 생성한다.",
        {
            "action_type": {"type": "string", "enum": ["freeze_account"]},
            "target_id": {"type": "string", "description": "지급정지를 요청할 계좌 ID"},
            "params": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "지급정지 요청 사유"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
        ["action_type", "target_id", "params"],
    ),
]


def call_tool(name: str, arguments: dict, role: str, session_id: str) -> dict:
    """온톨로지 메타데이터와 개발용 예시 응답을 반환한다."""
    if name == "describe_ontology":
        api_url = os.getenv("ONTOLOGY_API_URL", "http://localhost:8001").rstrip("/")
        response = requests.get(
            f"{api_url}/ontology",
            headers={"X-Role": role, "X-Session-Id": session_id},
            timeout=5,
        )
        response.raise_for_status()
        return response.json()

    if name == "request_action":
        if arguments.get("action_type") != "freeze_account":
            return {"detail": "지원하는 액션은 freeze_account입니다."}
        filename = "action_pending.json" if role == "manager" else "action_forbidden.json"
    else:
        filename = TOOL_FIXTURES.get(name)
        if filename is None:
            return {"detail": f"지원하지 않는 도구입니다: {name}"}

    result = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    if filename == "action_forbidden.json":
        result["detail"]["role"] = role
        result["detail"]["message"] = f"{role} 역할은 freeze_account를 요청할 수 없습니다. 허용 역할: manager"
    if name == "get_metric" and (
        arguments.get("name") != result["metric"]
        or arguments.get("period") != result["period"]
        or arguments.get("filters")
    ):
        return {"detail": "지표 예시는 필터 없는 2026년 3분기 active_customers만 제공합니다."}
    return result
