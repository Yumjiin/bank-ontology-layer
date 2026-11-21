"""온톨로지 도구를 전달하고 모델의 다음 응답을 받는다."""

import os

import requests

from agent.tools import TOOLS


def request_completion(messages: list[dict]) -> dict:
    """다음 답변 또는 도구 호출이 담긴 assistant 메시지를 반환한다."""
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if provider != "openai":
        raise ValueError("LLM_PROVIDER는 openai만 지원합니다.")

    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not model or not api_key:
        raise ValueError("LLM_MODEL과 LLM_API_KEY를 설정하세요.")

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": messages, "tools": TOOLS},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]
