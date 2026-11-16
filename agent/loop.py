"""에이전트 실행 루프 (담당: 수민).

설계서 7-2 흐름:
1. 시스템 프롬프트에 /ontology 요약과 규칙("지표 질문은 get_metric 우선", "SQL을 작성하지 말 것")을 넣음
2. 사용자 질문 → LLM이 도구 호출 결정
3. 도구 결과(정책 판정 포함)를 LLM에 다시 전달
4. 최대 MAX_STEPS 안에서 반복 후 최종 답변
5. LLM 호출(chat)과 도구 호출(execute_tool)을 agent_trace에 기록
"""

from __future__ import annotations

MAX_STEPS = 6


def run_agent(question: str, role: str, session_id: str) -> dict:
    """최종 답변과 단계별 기록을 반환함.

    반환 예시: {"answer": "...", "steps": [{"tool": "get_metric", "input": {...}, "output": {...}}]}
    """
    raise NotImplementedError("TODO(수민)")
