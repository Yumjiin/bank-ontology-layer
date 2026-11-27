"""조건 A·B 평가 자동화 (design.md 9장, eval/README.md).

질문별로 LLM이 SQL을 작성하고, DB에서 실행한 결과를 정답(questions.yaml의 answer)과 비교함.
- 조건 A: 질문 + 테이블 구조
- 조건 B: 질문 + 테이블 구조 + 정의 문서
프롬프트 형식은 발표 데모(data/reference/generate_mock_data.py의 write_prompts)를 따르고,
채점 방식은 발표 데모의 data/reference/check_answer.py와 같음 (1행 1열이면 그 값, 아니면 행 전체를 == 비교).

실행 (레포 루트)
  docker compose run --rm eval                         # A·B 각 3회
  docker compose run --rm eval --ids q01 q02 --runs 1  # 일부 문항만
  docker compose run --rm eval --dry-run               # LLM 대신 정답 SQL로 채점기 확인 (모두 정답이어야 함)
  docker compose run --rm eval --final                 # 발표에 쓸 최종 실행

결과 저장 위치
  eval/runs/     리허설·테스트 실행 (기본, git에 올리지 않음)
  eval/results/  --final로 실행한 최종 결과 (git에 올림)

필요한 환경변수 (.env): DATABASE_URL, LLM_MODEL, LLM_API_KEY
정답은 seed 적재 직후 기준이므로, 시연 등으로 데이터가 바뀌었다면 docker compose down -v 후 실행함.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg
import yaml

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.yaml"
DEFINITIONS_PATH = EVAL_DIR.parent / "data" / "reference" / "semantic_definitions.yaml"
RUNS_DIR = EVAL_DIR / "runs"        # 리허설·테스트 실행 결과 (.gitignore)
RESULTS_DIR = EVAL_DIR / "results"  # 최종 결과 (git에 올림)

# 원천 테이블 5개만 제공 (시스템 테이블 action_request, agent_trace 제외, 발표 데모 DDL과 같은 범위)
SOURCE_TABLES = ["brnc", "cust", "acct", "acct_rel", "txn"]

# 세 조건이 같은 날짜 맥락을 받도록, 조건 C 시스템 프롬프트(agent/loop.py)의 날짜 문장을 그대로 사용함
COMMON_HEADER = "기준 연도는 2026년입니다. 연도 없이 3분기를 묻는 경우 2026-07-01부터 2026-09-30까지입니다."

# 발표 원본 정의 문서에는 등급이 없음. q12(VIP)를 조건 B에서 공정하게 비교하기 위해 프롬프트에서만 덧붙임
GRADE_DEFINITION = """추가 정의 (발표 원본 정의 문서에 없는 항목)
codes:
  cust_grd_cd:
    "01": VIP
    "02": 일반"""

TAIL = "위 질문에 답하는 PostgreSQL SQL 쿼리 하나만 작성하세요. 설명 없이 SQL만 출력하세요."


# ---------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------

def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "postgresql://bank:bank@db:5432/bank")
    return dsn.replace("postgresql+psycopg://", "postgresql://")


def schema_ddl(conn: psycopg.Connection) -> str:
    """실제 DB에서 원천 테이블의 컬럼·타입·PK·FK만 뽑아 CREATE TABLE 형식으로 만듦.

    schema.sql을 그대로 주지 않는 이유: 코드값 의미가 주석으로 적혀 있어 조건 A에 정의가 새어 나감.
    """
    blocks = []
    for table in SOURCE_TABLES:
        columns = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        primary_key = [row[0] for row in conn.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "WHERE tc.table_schema = 'public' AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY' "
            "ORDER BY kcu.ordinal_position",
            (table,),
        )]
        foreign_keys = dict(conn.execute(
            "SELECT kcu.column_name, ccu.table_name || '(' || ccu.column_name || ')' "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema "
            "WHERE tc.table_schema = 'public' AND tc.table_name = %s AND tc.constraint_type = 'FOREIGN KEY'",
            (table,),
        ).fetchall())
        lines = [
            f"    {name} {dtype.upper()}" + (f" REFERENCES {foreign_keys[name]}" if name in foreign_keys else "")
            for name, dtype in columns
        ]
        lines.append(f"    PRIMARY KEY ({', '.join(primary_key)})")
        blocks.append(f"CREATE TABLE {table} (\n" + ",\n".join(lines) + "\n);")
    return "\n".join(blocks)


def build_prompt(condition: str, schema: str, definitions: str, question: str) -> str:
    parts = [COMMON_HEADER, "", "다음은 은행 데이터베이스(PostgreSQL)의 스키마입니다.", "", schema, ""]
    if condition == "B":
        parts += ["다음은 이 데이터베이스의 데이터 정의 문서입니다.", "", definitions, GRADE_DEFINITION, ""]
    parts += [f"질문: {question}", "", TAIL]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------
# LLM 호출과 SQL 실행·채점
# ---------------------------------------------------------------

def ask_llm(client, model: str, prompt: str) -> str:
    # 요청 옵션은 조건 C(agent/llm.py)와 같게 맞춤
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        reasoning_effort="none",
    )
    return response.choices[0].message.content or ""


def extract_sql(text: str) -> str:
    """코드 블록 표시를 벗겨냄 (check_answer.py의 처리와 같음)."""
    sql = text.strip().strip("`")
    if sql.lower().startswith("sql"):
        sql = sql[3:]
    return sql.strip()


def run_sql(conn: psycopg.Connection, sql: str):
    """SQL을 실행해 (값, 오류)를 반환함. 1행 1열이면 그 값, 아니면 행 전체 (check_answer.py의 run과 같음)."""
    try:
        rows = conn.execute(sql).fetchall()
        return (rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else rows), None
    except psycopg.Error as e:
        return None, f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
    finally:
        conn.rollback()  # 읽기 전용이지만 매 문항 실행 후 상태를 되돌림


def open_readonly(dsn: str) -> psycopg.Connection:
    """LLM이 쓴 SQL을 안전하게 실행하기 위한 연결: 읽기 전용 + 문장당 시간 제한."""
    conn = psycopg.connect(dsn)
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '10s'")
    conn.commit()
    return conn


# ---------------------------------------------------------------
# 실행과 결과 정리
# ---------------------------------------------------------------

def pad(text: str, width: int) -> str:
    """한글은 화면에서 2칸을 차지하므로 표시 폭 기준으로 공백을 채움."""
    shown = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    return text + " " * max(width - shown, 0)


def summarize(records: list[dict], conditions: list[str]) -> None:
    print("\n[조건별 정답률]")
    for cond in conditions:
        rs = [r for r in records if r["condition"] == cond]
        ok = sum(r["correct"] for r in rs)
        latency = sum(r["latency_s"] for r in rs) / len(rs)
        print(f"  {cond}: {ok}/{len(rs)} ({ok / len(rs):.0%})  평균 소요 {latency:.2f}초")

    print("\n[태그별 정답률]  (태그 정의는 questions.yaml 상단 주석 참고)")
    by_tag: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        for tag in r["tags"]:
            by_tag[tag][r["condition"]].append(r["correct"])
    for tag, per_cond in by_tag.items():
        cells = "  ".join(
            f"{cond} {sum(v) / len(v):.0%}" for cond in conditions if (v := per_cond.get(cond))
        )
        print(f"  {pad(tag, 10)} {cells}")


def main() -> int:
    parser = argparse.ArgumentParser(description="조건 A·B 평가")
    parser.add_argument("--conditions", nargs="+", default=["A", "B"], choices=["A", "B"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--ids", nargs="+", help="일부 문항만 실행 (예: q01 q02)")
    parser.add_argument("--dry-run", action="store_true", help="LLM 대신 정답 SQL로 채점기 확인")
    parser.add_argument("--final", action="store_true", help="최종 결과로 eval/results/에 저장 (기본은 eval/runs/)")
    args = parser.parse_args()

    questions = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    definitions = DEFINITIONS_PATH.read_text(encoding="utf-8")

    client, model = None, "dry-run"
    if not args.dry_run:
        from openai import OpenAI  # dry-run에서는 필요 없음
        model = os.environ["LLM_MODEL"]
        client = OpenAI(api_key=os.environ["LLM_API_KEY"])

    conn = open_readonly(get_dsn())
    schema = schema_ddl(conn)
    conn.rollback()

    records = []
    started = datetime.now().isoformat(timespec="seconds")
    for cond in args.conditions:
        for run in range(1, args.runs + 1):
            for q in questions:
                prompt = build_prompt(cond, schema, definitions, q["question"])
                t0 = time.perf_counter()
                raw = q["answer_sql"] if args.dry_run else ask_llm(client, model, prompt)
                latency = time.perf_counter() - t0
                sql = extract_sql(raw)
                value, error = run_sql(conn, sql)
                correct = error is None and value == q["answer"]
                records.append({
                    "condition": cond, "run": run, "id": q["id"], "tags": q["tags"],
                    "answer": q["answer"], "value": value, "correct": correct, "error": error,
                    "sql": sql, "latency_s": round(latency, 3),
                })
                mark = "O" if correct else "X"
                print(f"{cond} {run}회 {q['id']} {mark}  결과={value!r:.40}  정답={q['answer']!r}"
                      + (f"  ({error})" if error else ""))
    conn.close()

    summarize(records, args.conditions)

    if args.final and args.dry_run:
        print("--final은 --dry-run과 함께 쓸 수 없어 eval/runs/에 저장함")
    out_dir = RESULTS_DIR if args.final and not args.dry_run else RUNS_DIR
    out_dir.mkdir(exist_ok=True)
    prefix = "dryrun" if args.dry_run else "ab"
    out = out_dir / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({
        "meta": {"started_at": started, "model": model, "conditions": args.conditions,
                 "runs": args.runs, "dry_run": args.dry_run, "common_header": COMMON_HEADER},
        "records": records,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n결과 저장: {out.relative_to(EVAL_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
