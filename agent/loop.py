"""개발용 예시 응답을 사용하는 에이전트 루프."""

import json

import requests

from agent.llm import request_completion
from agent.tools import call_tool

MAX_STEPS = 6
SYSTEM_PROMPT = """은행 온톨로지 질문에 한국어로 답하세요.
객체나 링크 정의가 필요하면 describe_ontology를 호출하세요.
활성 고객 수(active_customers) 같은 지표 질문은 get_metric을 우선 사용하세요.
연도 없이 3분기를 묻는 데모 질문의 기간은 2026-07-01부터 2026-09-30까지입니다.
SQL을 작성하거나 공개하지 마세요. 정책 판정과 권한 거부 사유를 반영하세요.
조회 및 액션 도구의 결과는 개발용 예시입니다. 예시라는 점을 답변에 명시하세요.
질문의 대상이나 조건과 다른 예시는 질문의 결과로 제시하지 마세요."""


def run_agent(question: str, role: str, session_id: str) -> dict:
    """최종 답변과 단계별 기록을 반환함.

    반환 예시: {"answer": "...", "steps": [{"tool": "get_metric", "input": {...}, "output": {...}}]}
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    steps = []
    for _ in range(MAX_STEPS):
        message = request_completion(messages)
        calls = message.get("tool_calls") or []
        if not calls:
            answer = message.get("content") or message.get("refusal")
            return {"answer": answer or "모델이 답변을 반환하지 않았습니다.", "steps": steps}

        if len(steps) + len(calls) > MAX_STEPS:
            break

        messages.append({
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": calls,
        })
        for call in calls:
            function = call["function"]
            name = function["name"]
            arguments, output = _run_tool(name, function["arguments"], role, session_id)
            steps.append({"tool": name, "input": arguments, "output": output})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(
                    {key: value for key, value in output.items() if key != "sql"},
                    ensure_ascii=False,
                ),
            })

    return {"answer": "호출 횟수 한도에 도달해 답변을 완료하지 못했습니다.", "steps": steps}


def _run_tool(name: str, raw_arguments: str, role: str, session_id: str) -> tuple:
    try:
        arguments = json.loads(raw_arguments)
    except ValueError:
        arguments = raw_arguments
    if not isinstance(arguments, dict):
        return arguments, {"detail": "도구 인수를 JSON 객체로 전달하세요."}

    try:
        return arguments, call_tool(name, arguments, role, session_id)
    except requests.RequestException:
        return arguments, {"detail": "온톨로지 서비스에서 메타데이터를 가져오지 못했습니다."}
