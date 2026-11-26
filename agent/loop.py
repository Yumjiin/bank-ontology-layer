"""온톨로지 서비스에 연결된 에이전트 루프."""

import json
from time import perf_counter
from uuid import uuid4

import requests

from agent.llm import request_completion
from agent.tools import call_tool, service_request

MAX_STEPS = 6
SYSTEM_PROMPT = """은행 온톨로지 질문에 한국어로 답하세요.
아래 온톨로지 요약을 사용하세요. 요약을 받지 못했거나 필요한 정의가 빠져 있을 때만 describe_ontology를 호출하세요.
조건에 맞는 대상이 몇 명 또는 몇 건인지 묻는 단순 개수 질문에는 search_objects 또는 get_linked_objects의 total을 답으로 쓰세요.
total이 없으면 전체 개수를 확인할 수 없다고 답하세요. count는 응답에 포함된 항목 수입니다.
get_metric은 활성 고객 수(active_customers), 순입금액(net_deposit)처럼 업무 정의가 필요한 지표에만 사용하세요.
지표 필터는 온톨로지 요약에서 해당 지표의 filterable_objects에 나열된 객체의 속성만 사용하세요.
기준 연도는 2026년입니다. 연도 없이 3분기를 묻는 경우 2026-07-01부터 2026-09-30까지입니다.
400 오류(INVALID_REQUEST)에 허용값이 오면 detail.message를 참고해 인수를 고쳐 다시 시도하세요.
마케팅 역할에서 403 오류(FORBIDDEN_ROLE)가 발생하면 detail.message를 그대로 사용자에게 설명하고, 같은 정보를 다른 속성이나 도구로 우회 조회하지 마세요.
SQL을 작성하거나 공개하지 마세요. 정책 판정과 권한 거부 사유를 반영하세요.
서비스가 오류를 반환하면 조회 또는 요청이 성공했다고 답하지 마세요."""


def run_agent(question: str, role: str, session_id: str) -> dict:
    """최종 답변과 단계별 기록을 반환함.

    반환 예시: {"answer": "...", "steps": [{"tool": "get_metric", "input": {...}, "output": {...}}]}
    """
    trace_id = f"t-{uuid4().hex}"
    trace_step = 0
    trace_saved = True

    def record(span_type: str, summary: str, **details):
        nonlocal trace_step, trace_saved
        trace_step += 1
        try:
            result = service_request("POST", "/traces", role, session_id, {
                "trace_id": trace_id,
                "step": trace_step,
                "span_type": span_type,
                "output_summary": summary,
                **details,
            })
            if "detail" in result:
                trace_saved = False
        except requests.RequestException:
            trace_saved = False

    def run_tool(name: str, raw_arguments: str) -> tuple:
        started = perf_counter()
        arguments, output = _run_tool(name, raw_arguments, role, session_id)
        policy = output.get("policy") or {}
        detail = output.get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        decision = None
        if code == "FORBIDDEN_ROLE" or policy.get("blocked"):
            decision = "BLOCK"
        elif policy.get("masked"):
            decision = "MASK"
        elif "policy" in output:
            decision = "ALLOW"
        record(
            "execute_tool",
            (code or "도구 호출 실패") if "detail" in output else "도구 호출 완료",
            tool_name=name,
            input=arguments,
            sql_text=output.get("sql"),
            policy_decision=decision,
            latency_ms=round((perf_counter() - started) * 1000),
        )
        return arguments, output

    record("invoke_agent", "에이전트 실행 시작")
    _, ontology = run_tool("describe_ontology", "{}")
    system_prompt = f"{SYSTEM_PROMPT}\n현재 사용자 역할은 {role}입니다."
    if "detail" not in ontology:
        system_prompt += "\n\n온톨로지 요약\n" + json.dumps(ontology, ensure_ascii=False)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    steps = []
    for _ in range(MAX_STEPS):
        started = perf_counter()
        try:
            message = request_completion(messages)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            record("chat", "모델 호출 실패", latency_ms=round((perf_counter() - started) * 1000))
            raise
        calls = message.get("tool_calls") or []
        record(
            "chat",
            f"도구 호출 {len(calls)}건 결정" if calls else "최종 답변 생성",
            latency_ms=round((perf_counter() - started) * 1000),
        )
        if not calls:
            answer = message.get("content") or message.get("refusal")
            return {
                "answer": answer or "모델이 답변을 반환하지 않았습니다.",
                "steps": steps,
                "trace_id": trace_id,
                "trace_saved": trace_saved,
            }

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
            arguments, output = run_tool(name, function["arguments"])
            steps.append({"tool": name, "input": arguments, "output": output})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(
                    {key: value for key, value in output.items() if key != "sql"},
                    ensure_ascii=False,
                ),
            })

    return {
        "answer": "호출 횟수 한도에 도달해 답변을 완료하지 못했습니다.",
        "steps": steps,
        "trace_id": trace_id,
        "trace_saved": trace_saved,
    }


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
        return arguments, {"detail": "온톨로지 서비스에서 도구 요청을 처리하지 못했습니다."}
