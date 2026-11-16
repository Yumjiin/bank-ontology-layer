"""에이전트가 사용할 도구 정의 (담당: 수민).

LLM에는 아래 도구만 제공하고, SQL은 절대 직접 작성하지 않게 함.
각 도구는 Ontology Service API를 호출함. 계약은 docs/api-contract.md 참고.

TODO(수민):
- 사용할 LLM 제공자의 function calling 형식으로 TOOLS를 변환
- call_tool()에서 Ontology Service를 호출하도록 구현 (X-Role, X-Session-Id 헤더 포함)
- 서비스 구현 전에는 docs/fixtures/의 예시 JSON을 반환하는 모드로 개발
"""

from __future__ import annotations

import os

ONTOLOGY_API_URL = os.environ.get("ONTOLOGY_API_URL", "http://localhost:8001")

# 도구 이름 → (HTTP 메서드, 경로) 매핑. 설계서 7-1 기준
TOOLS = {
    "describe_ontology": ("GET", "/ontology"),
    "search_objects": ("POST", "/objects/search"),
    "get_linked_objects": ("POST", "/objects/{object_type}/{object_id}/links/{link}"),
    "get_metric": ("POST", "/metrics/{name}"),
    "request_action": ("POST", "/actions/{action_type}"),
}


def call_tool(name: str, arguments: dict, role: str, session_id: str) -> dict:
    """도구 호출을 Ontology Service 요청으로 바꿔 실행하고 결과를 반환함."""
    raise NotImplementedError("TODO(수민)")
