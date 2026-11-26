"""역할별 접근 정책 (Foundry의 Markings / Object-level Permission 역할).

ontology.yaml에서 속성마다 sensitivity(예: PII)를 붙이고, roles에서 역할별로
어떤 sensitivity를 마스킹(mask)하거나 차단(block)할지 정의함.

| 규칙   | 응답에서                          | 필터로 사용 |
| ------ | --------------------------------- | ----------- |
| mask   | 값을 "***"로 바꿈 (속성은 남김)   | 불가 (403)  |
| block  | 속성을 아예 뺌                    | 불가 (403)  |

필터를 막는 이유: 이름을 못 보는 역할이라도 name = '김하나'로 검색할 수 있으면
결과 유무로 그 고객의 존재와 다른 정보를 알아낼 수 있음.

지표(집계값)는 개인을 식별하지 않으므로 값은 그대로 두고, 필터 제한만 똑같이 적용함.
"""

from __future__ import annotations

from typing import Any, Iterable

from metadata import Ontology

MASK = "***"


class PolicyError(PermissionError):
    """역할이 허용하지 않는 요청. API에서는 403(FORBIDDEN_ROLE)으로 반환함."""

    def __init__(self, message: str, role: str):
        super().__init__(message)
        self.role = role


def restricted_properties(ontology: Ontology, obj_name: str, role: str) -> tuple[set[str], set[str]]:
    """해당 역할이 obj_name에서 마스킹해야 하는 속성과 차단해야 하는 속성."""
    rule = ontology.roles.get(role)
    if rule is None:
        raise PolicyError(f"정의되지 않은 역할 '{role}'. 사용 가능: {', '.join(ontology.roles)}", role)
    obj = ontology.object_types[obj_name]
    masked = {n for n, p in obj.properties.items() if p.sensitivity and p.sensitivity in rule.mask}
    blocked = {n for n, p in obj.properties.items() if p.sensitivity and p.sensitivity in rule.block}
    return masked - blocked, blocked  # 둘 다 해당하면 더 강한 차단을 적용


def check_filters(ontology: Ontology, role: str, targets: Iterable[tuple[str, str]]) -> None:
    """(객체, 속성) 필터 목록 중 역할이 쓸 수 없는 것이 있으면 PolicyError."""
    for obj_name, prop in targets:
        masked, blocked = restricted_properties(ontology, obj_name, role)
        if prop in masked or prop in blocked:
            sensitivity = ontology.object_types[obj_name].properties[prop].sensitivity
            raise PolicyError(
                f"{role} 역할은 {sensitivity} 속성 {obj_name}.{prop}로 검색할 수 없음 "
                f"(값을 볼 수 없는 속성은 조건으로도 사용할 수 없음)",
                role,
            )


def apply_to_items(
    ontology: Ontology, obj_name: str, role: str, items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """조회 결과에 마스킹·차단을 적용하고, 응답의 policy 필드를 만듦.

    policy에는 이번 응답에 실제로 들어 있던 속성만 기록함 (요청하지 않은 속성까지 나열하지 않음).
    """
    masked, blocked = restricted_properties(ontology, obj_name, role)
    present = set().union(*(item.keys() for item in items)) if items else set()
    if not items:
        # 결과가 없어도 어떤 속성이 제한되는지는 알려줌
        present = set(ontology.object_types[obj_name].properties)

    out = []
    for item in items:
        row = {}
        for key, value in item.items():
            if key in blocked:
                continue
            row[key] = MASK if key in masked and value is not None else value
        out.append(row)

    return out, {"masked": sorted(masked & present), "blocked": sorted(blocked & present)}


def empty_policy() -> dict[str, list[str]]:
    """개인 단위 값이 없는 응답(지표)용."""
    return {"masked": [], "blocked": []}
