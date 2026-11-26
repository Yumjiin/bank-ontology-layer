"""ontology.yaml 코드값 정합성 확인 (design.md 4-3 정의 검증의 '코드값' 항목).

ontology.yaml에서 values가 정의된 속성마다, 실제 DB 컬럼의 DISTINCT 값과 비교함.
- 누락: 데이터에는 있는데 values에 없는 코드 → 정의가 빠진 것이므로 문제. 하나라도 있으면 종료 코드 1
- 미사용: values에는 있는데 데이터에는 없는 코드 → 문제 아님, 참고로 출력
          (예: F 지급정지는 freeze_account 승인·실행 후에만 생김)

실행 (레포 루트에서, seed 컨테이너엔 ontology.yaml이 없어 실행할 때 폴더를 연결함)
  docker compose build seed
  docker compose run --rm -v "${PWD}/ontology:/app/ontology:ro" seed python check_codes.py

서비스 기동 시 경고로 연결할 때는 check_codes()만 가져다 쓰면 됨 (출력과 분리되어 있음).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import yaml
from psycopg import sql

from seed import get_dsn


@dataclass
class CodeCheck:
    """속성 하나의 비교 결과."""

    object_type: str
    prop: str
    table: str
    column: str
    defined: set[str]
    actual: set[str]
    missing: list[str] = field(default_factory=list)  # 데이터에만 있음 (문제)
    unused: list[str] = field(default_factory=list)   # 정의에만 있음 (참고)


def default_ontology_path() -> Path:
    """ONTOLOGY_PATH 환경변수 → 로컬 레포 경로 → 컨테이너 마운트 경로 순으로 찾음."""
    if os.environ.get("ONTOLOGY_PATH"):
        return Path(os.environ["ONTOLOGY_PATH"])
    local = Path(__file__).resolve().parents[1] / "ontology" / "ontology.yaml"
    return local if local.exists() else Path("/app/ontology/ontology.yaml")


def check_codes(conn: psycopg.Connection, ontology: dict) -> list[CodeCheck]:
    """values가 있는 모든 속성에 대해 정의된 코드와 실제 DISTINCT 값을 비교함."""
    results: list[CodeCheck] = []
    for object_type, obj in ontology["object_types"].items():
        for prop, spec in obj["properties"].items():
            if not spec.get("values"):
                continue
            # 테이블·컬럼명은 yaml에서 오므로 식별자로 안전하게 감쌈
            query = sql.SQL("SELECT DISTINCT {} FROM {}").format(
                sql.Identifier(spec["column"]), sql.Identifier(obj["table"])
            )
            # NULL은 코드값이 아니므로 비교에서 제외함
            actual = {str(row[0]) for row in conn.execute(query).fetchall() if row[0] is not None}
            defined = {str(code) for code in spec["values"]}
            results.append(CodeCheck(
                object_type=object_type, prop=prop,
                table=obj["table"], column=spec["column"],
                defined=defined, actual=actual,
                missing=sorted(actual - defined),
                unused=sorted(defined - actual),
            ))
    return results


def print_report(results: list[CodeCheck]) -> None:
    print("[코드값 확인] ontology.yaml values ⊇ 실제 데이터 DISTINCT 값")
    for r in results:
        status = "누락" if r.missing else "OK  "  # 한글은 화면에서 2칸이라 길이를 맞춤
        line = (f"  {status} {r.object_type + '.' + r.prop:<22} {r.table + '.' + r.column:<18}"
                f" 정의 {len(r.defined)}개 / 데이터 {len(r.actual)}개")
        if r.missing:
            line += f"  → 정의에 없는 코드: {', '.join(r.missing)}"
        if r.unused:
            line += f"  (참고: 데이터에 없는 코드 {', '.join(r.unused)})"
        print(line)
    missing_total = sum(len(r.missing) for r in results)
    print(f"결과: 속성 {len(results)}개 검사, 누락 코드 {missing_total}건")


def main() -> int:
    path = default_ontology_path()
    ontology = yaml.safe_load(path.read_text(encoding="utf-8"))
    with psycopg.connect(get_dsn()) as conn:
        results = check_codes(conn, ontology)
    print_report(results)
    return 1 if any(r.missing for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
