"""OpenAI function calling에 제공하는 온톨로지 도구 정의."""

import os
from urllib.parse import quote

import requests

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
        "필터에 맞는 객체를 검색한다. 전체 개수는 응답의 total을 사용한다.",
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
        "기준 객체의 링크를 따라 연결된 객체를 조회한다. 전체 개수는 응답의 total을 사용한다.",
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
        "활성 고객 수, 순입금액 등 업무 정의가 필요한 온톨로지 지표를 계산한다.",
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


def service_request(method: str, path: str, role: str, session_id: str, body=None) -> dict:
    api_url = os.getenv("ONTOLOGY_API_URL", "http://localhost:8001").rstrip("/")
    response = requests.request(
        method,
        f"{api_url}{path}",
        headers={"X-Role": role, "X-Session-Id": session_id},
        json=body,
        timeout=5,
    )
    if response.status_code not in (400, 403, 404, 409, 422, 501, 503):
        response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise requests.RequestException("잘못된 온톨로지 서비스 응답 형식")
    return result


TOOL_ROUTES = {
    "search_objects": ("/objects/search", ()),
    "get_linked_objects": ("/objects/{object_type}/{object_id}/links/{link}", ("object_type", "object_id", "link")),
    "get_metric": ("/metrics/{name}", ("name",)),
    "request_action": ("/actions/{action_type}", ("action_type",)),
}


def call_tool(name: str, arguments: dict, role: str, session_id: str) -> dict:
    """도구 인수를 온톨로지 서비스 API 요청으로 변환한다."""
    if name == "describe_ontology":
        return service_request("GET", "/ontology", role, session_id)

    if name not in TOOL_ROUTES:
        return {"detail": f"지원하지 않는 도구입니다: {name}"}

    route, path_keys = TOOL_ROUTES[name]
    body = arguments.copy()
    path_values = {}
    for key in path_keys:
        value = body.pop(key, None)
        if not isinstance(value, str) or not value.strip():
            return {"detail": f"{key}에 비어 있지 않은 문자열을 전달하세요."}
        path_values[key] = quote(value, safe="")
    return service_request("POST", route.format(**path_values), role, session_id, body)
