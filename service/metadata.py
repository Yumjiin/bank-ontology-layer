"""온톨로지 정의 로드와 검증 (Foundry의 Ontology Metadata Service 역할).

ontology.yaml을 Pydantic 모델로 읽고, 정의끼리 서로 참조가 맞는지 확인함.
DB 정합성 검증(테이블·컬럼 존재 여부)은 TODO.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

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


# link via.condition에 허용하는 형식: "<관계 테이블 컬럼> = '<값>'"  예) "rel_cd = '01'"
# 임의 SQL을 막기 위해 이 형식만 허용하고, compiler가 파싱해서 바인드 파라미터로 바꿈
VIA_CONDITION = re.compile(r"^\s*(\w+)\s*=\s*'([^']*)'\s*$")


class MetricFilter(BaseModel):
    """지표 정의에 고정된 조건. property는 "객체.속성", value는 사람이 쓰는 값."""

    property: str
    op: Literal["eq", "in", "gte", "lte", "between"]
    value: Any


class MetricSign(BaseModel):
    """합계에서 부호를 뒤집을 조건. 예) 거래 유형이 출금이면 빼기."""

    property: str
    negative: list[str]


class MetricMeasure(BaseModel):
    agg: Literal["count", "count_distinct", "sum"]
    property: str | None = None  # count는 생략 가능, count_distinct·sum은 필수
    sign: MetricSign | None = None

    @model_validator(mode="after")
    def check_property(self) -> "MetricMeasure":
        if self.agg != "count" and not self.property:
            raise ValueError(f"measure.agg가 {self.agg}이면 property가 필요함")
        if self.sign and self.agg != "sum":
            raise ValueError("measure.sign은 agg가 sum일 때만 사용함")
        return self


class Metric(BaseModel):
    description: str
    object: str
    path: list[str] = Field(default_factory=list)
    period: str  # period 파라미터를 적용할 날짜 속성 ("객체.속성")
    filters: list[MetricFilter] = Field(default_factory=list)
    measure: MetricMeasure
    unit: str = ""


class ActionEffect(BaseModel):
    """액션이 실행될 때 대상 객체에서 바뀌는 내용. 값은 사람이 쓰는 값."""

    property: str
    value: str
    allowed_from: list[str] = Field(default_factory=list)  # 비어 있으면 현재 값과 무관하게 실행


class ActionType(BaseModel):
    description: str
    target: str
    params: dict[str, Literal["string"]] = Field(default_factory=dict)  # 지금은 문자열 파라미터만 지원
    allowed_roles: list[str]                                          # 요청할 수 있는 역할
    approver_roles: list[str] = Field(default_factory=list)            # 승인·거절할 수 있는 역할 (비우면 allowed_roles)
    requires_approval: bool = True
    effect: ActionEffect

    @property
    def approvers(self) -> list[str]:
        return self.approver_roles or self.allowed_roles


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

    def path_objects(self, metric: Metric) -> list[str]:
        """지표 경로에 등장하는 객체 타입 목록. 예) active_customers → [Customer, Account, Transaction]"""
        objects = [metric.object]
        for step in metric.path:
            link = self.link_types.get(step)
            if link is None:
                break
            objects.append(link.to)
        return objects

    def _qualified_property_error(self, where: str, ref: str, allowed: list[str], need_type: str | None = None) -> str | None:
        obj_name, _, prop_name = ref.partition(".")
        if not prop_name:
            return f"{where}: '{ref}'는 '객체.속성' 형식이어야 함"
        if obj_name not in allowed:
            return f"{where}: '{obj_name}'는 지표 경로({', '.join(allowed)})에 없는 객체"
        prop = self.object_types[obj_name].properties.get(prop_name)
        if prop is None:
            return f"{where}: {obj_name}에 속성 '{prop_name}'이 없음"
        if need_type and prop.type != need_type:
            return f"{where}: {ref}는 {need_type} 타입이어야 함 (현재 {prop.type})"
        return None

    def reference_errors(self) -> list[str]:
        """정의 간 참조 오류 목록을 반환함. 비어 있으면 정상."""
        errors: list[str] = []
        objects = self.object_types

        for name, obj in objects.items():
            if obj.primary_key not in {p.column for p in obj.properties.values()}:
                errors.append(f"object_types.{name}: primary_key '{obj.primary_key}'가 properties의 column에 없음")

        for name, link in self.link_types.items():
            missing_side = False
            for side in (link.from_, link.to):
                if side not in objects:
                    errors.append(f"link_types.{name}: 존재하지 않는 객체 타입 '{side}'")
                    missing_side = True
            if missing_side:
                continue
            if link.join:
                for side, key in ((link.from_, link.join.from_key), (link.to, link.join.to_key)):
                    if key not in {p.column for p in objects[side].properties.values()}:
                        errors.append(f"link_types.{name}: join 키 '{key}'가 {side}의 속성 컬럼에 없음")
            if link.via and link.via.condition and not VIA_CONDITION.match(link.via.condition):
                errors.append(f"link_types.{name}: via.condition '{link.via.condition}'는 \"컬럼 = '값'\" 형식만 허용함")

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
            if metric.object not in objects or any(step not in self.link_types for step in metric.path):
                continue
            allowed = self.path_objects(metric)
            where = f"metrics.{name}"
            checks = [(f"{where}.period", metric.period, "date")]
            checks += [(f"{where}.filters", f.property, None) for f in metric.filters]
            if metric.measure.property:
                need = "number" if metric.measure.agg == "sum" else None
                checks.append((f"{where}.measure.property", metric.measure.property, need))
            if metric.measure.sign:
                checks.append((f"{where}.measure.sign", metric.measure.sign.property, None))
            for label, ref, need in checks:
                err = self._qualified_property_error(label, ref, allowed, need)
                if err:
                    errors.append(err)

        for name, action in self.action_types.items():
            if action.target not in objects:
                errors.append(f"action_types.{name}: 존재하지 않는 객체 타입 '{action.target}'")
                continue
            for role in set(action.allowed_roles) | set(action.approver_roles):
                if role not in self.roles:
                    errors.append(f"action_types.{name}: 존재하지 않는 역할 '{role}'")
            prop = objects[action.target].properties.get(action.effect.property)
            if prop is None:
                errors.append(f"action_types.{name}.effect: {action.target}에 속성 '{action.effect.property}'이 없음")
                continue
            if prop.values:
                allowed = set(prop.values.values())
                for v in [action.effect.value, *action.effect.allowed_from]:
                    if v not in allowed:
                        errors.append(f"action_types.{name}.effect: '{v}'는 {action.target}.{action.effect.property}의 정의된 값이 아님")

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
                name: {
                    "description": m.description,
                    "params": ["period"],
                    "unit": m.unit,
                    "filterable_objects": self.path_objects(m),  # filters에 쓸 수 있는 객체 (예: Transaction.channel)
                }
                for name, m in self.metrics.items()
            },
            "action_types": {
                name: {
                    "description": a.description,
                    "target": a.target,
                    "params": a.params,
                    "allowed_roles": a.allowed_roles,
                    "requires_approval": a.requires_approval,
                    "effect": f"{a.target}.{a.effect.property} → {a.effect.value}",
                }
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
