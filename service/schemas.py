"""API 요청 형식. 에이전트(수민)와 서비스(지인)가 공유하는 계약.

변경 시 docs/api-contract.md와 docs/fixtures/도 함께 수정함.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Filter(BaseModel):
    """LLM이 임의 SQL을 넣지 못하도록 제한된 필터 표현만 허용함.
    value는 사람이 쓰는 표현(VIP, 입금, 2026-07-01)으로 받고 서비스가 원천 코드로 변환함."""

    property: str = Field(examples=["grade"])
    op: Literal["eq", "in", "gte", "lte", "between"]
    value: Any = Field(examples=["VIP"])


class SearchRequest(BaseModel):
    object_type: str = Field(examples=["Customer"])
    filters: list[Filter] = Field(default_factory=list)
    properties: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=200)


class LinkRequest(BaseModel):
    filters: list[Filter] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)


class Period(BaseModel):
    start: str = Field(examples=["2026-07-01"])
    end: str = Field(examples=["2026-09-30"])


class MetricRequest(BaseModel):
    period: Period
    filters: list[Filter] = Field(default_factory=list)


class ActionRequest(BaseModel):
    target_id: str = Field(examples=["1002-001-000123"])
    params: dict[str, Any] = Field(default_factory=dict, examples=[{"reason": "보이스피싱 의심"}])
