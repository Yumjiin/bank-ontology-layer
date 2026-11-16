"""온톨로지 정의 로드와 검증 (Foundry의 Ontology Metadata Service 역할).

ontology.yaml을 Pydantic 모델로 읽고, 정의끼리 서로 참조가 맞는지 확인함.
DB 정합성 검증(테이블·컬럼 존재 여부)은 TODO.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Property(BaseModel):
    column: str
    type: str = "string"
    format: str | None = None
    values: dict[str, str] | None = None
    sensitivity: str | None = None

    model_config = ConfigDict(coerce_numbers_to_str=True)


class ObjectType(BaseModel):
    table: str
    primary_key: str
    description: str = ""
    properties: dict[str, Property]
    default_filters: list[str] = Field(default_factory=list)


class LinkVia(BaseModel):
    table: str
    from_key: str
    to_key: str
    condition: str | None = None


class LinkJoin(BaseModel):
    from_key: str
    to_key: str


class LinkType(BaseModel):
    description: str = ""
    from_: str = Field(alias="from")
    to: str
    via: LinkVia | None = None
    join: LinkJoin | None = None

    @model_validator(mode="after")
    def check_join_style(self) -> "LinkType":
        if (self.via is None) == (self.join is None):
            raise ValueError("link_type은 via와 join 중 정확히 하나를 가져야 함")
        return self


class Metric(BaseModel):
    description: str
    object: str
    path: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    aggregate: str | None = None
    expression: str | None = None
    params: list[str] = Field(default_factory=list)


class ActionType(BaseModel):
    description: str
    target: str
    params: dict[str, str] = Field(default_factory=dict)
    allowed_roles: list[str]
    requires_approval: bool = True


class Role(BaseModel):
    description: str = ""
    mask: list[str] = Field(default_factory=list)
    block: list[str] = Field(default_factory=list)


class Ontology(BaseModel):
    version: str
    object_types: dict[str, ObjectType]
    link_types: dict[str, LinkType] = Field(default_factory=dict)
    metrics: dict[str, Metric] = Field(default_factory=dict)
    action_types: dict[str, ActionType] = Field(default_factory=dict)
    roles: dict[str, Role]

    def reference_errors(self) -> list[str]:
        """정의 간 참조 오류 목록을 반환함. 비어 있으면 정상."""
        errors: list[str] = []
        objects = self.object_types

        for name, obj in objects.items():
            if obj.primary_key not in {p.column for p in obj.properties.values()}:
                errors.append(f"object_types.{name}: primary_key '{obj.primary_key}'가 properties의 column에 없음")

        for name, link in self.link_types.items():
            for side in (link.from_, link.to):
                if side not in objects:
                    errors.append(f"link_types.{name}: 존재하지 않는 객체 타입 '{side}'")

        for name, metric in self.metrics.items():
            if metric.object not in objects:
                errors.append(f"metrics.{name}: 존재하지 않는 객체 타입 '{metric.object}'")
            current = metric.object
            for step in metric.path:
                link = self.link_types.get(step)
                if link is None:
                    errors.append(f"metrics.{name}: 존재하지 않는 링크 '{step}'")
                    break
                if link.from_ != current:
                    errors.append(f"metrics.{name}: 링크 '{step}'는 {link.from_}에서 시작하지만 경로상 현재 객체는 {current}")
                    break
                current = link.to

        for name, action in self.action_types.items():
            if action.target not in objects:
                errors.append(f"action_types.{name}: 존재하지 않는 객체 타입 '{action.target}'")
            for role in action.allowed_roles:
                if role not in self.roles:
                    errors.append(f"action_types.{name}: 존재하지 않는 역할 '{role}'")

        return errors

    def summary(self) -> dict:
        """에이전트 시스템 프롬프트와 /ontology 응답에 쓰는 요약."""
        return {
            "version": self.version,
            "object_types": {
                name: {
                    "description": obj.description,
                    "properties": {
                        p: {"type": prop.type, "values": list(prop.values.values()) if prop.values else None}
                        for p, prop in obj.properties.items()
                    },
                }
                for name, obj in self.object_types.items()
            },
            "link_types": {
                name: {"description": link.description, "from": link.from_, "to": link.to}
                for name, link in self.link_types.items()
            },
            "metrics": {
                name: {"description": m.description, "params": m.params}
                for name, m in self.metrics.items()
            },
            "action_types": {
                name: {"description": a.description, "target": a.target, "params": a.params}
                for name, a in self.action_types.items()
            },
            "roles": list(self.roles),
        }


class OntologyError(Exception):
    pass


def load_ontology(path: str | Path) -> Ontology:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ontology = Ontology.model_validate(raw)
    errors = ontology.reference_errors()
    if errors:
        raise OntologyError("ontology.yaml 검증 실패:\n- " + "\n- ".join(errors))
    return ontology
