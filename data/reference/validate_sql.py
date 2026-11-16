"""
SQL 검증 스크립트 (BOAZ 발표 ④ 검증 루프 데모용)

정답을 모르는 상태에서도 할 수 있는 검사만 수행함.
  1. 실행 검사:    SQL이 오류 없이 실행되는가
  2. 결과 검사:    결과가 비어 있거나 0·NULL이 아닌가
  3. 값 존재 검사: WHERE·CASE 조건의 값이 실제 컬럼에 존재하는가
  4. 형식 검사:    날짜 등 범위 조건의 값이 실제 데이터와 같은 형식인가

문제가 발견되면 LLM에 그대로 붙여넣을 수 있는 피드백 문장을 출력함.

사용법
  python validate_sql.py sql_A_net.sql        (macOS는 python3)
"""
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "bank_demo.db"
LIST_LIMIT = 20  # 서로 다른 값이 이 개수 이하인 컬럼은 실제 값 목록을 피드백에 넣음

COL = r"(?:\w+\.)?(\w+)"
EQ_PATTERN = re.compile(COL + r"\s*(?:=|<>|!=)\s*'([^']*)'", re.I)
IN_PATTERN = re.compile(COL + r"\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", re.I)
RANGE_PATTERN = re.compile(COL + r"\s*(?:>=|<=|>|<)\s*'([^']*)'", re.I)
BETWEEN_PATTERN = re.compile(COL + r"\s+BETWEEN\s+'([^']*)'\s+AND\s+'([^']*)'", re.I)


def load_sql(path):
    sql = Path(path).read_text(encoding="utf-8").strip().strip("`")
    if sql.lower().startswith("sql"):
        sql = sql[3:]
    return sql.replace("\\", "")  # 채팅 화면 복사 시 딸려오는 마크다운 이스케이프 제거


def column_tables(con):
    """컬럼명 -> 그 컬럼을 가진 테이블 목록"""
    mapping = {}
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        for row in con.execute(f"PRAGMA table_info({t})"):
            mapping.setdefault(row[1].lower(), []).append(t)
    return mapping


def shape(value):
    """값의 형식만 남김: 숫자는 9, 문자는 A (예: '2026-09-01' -> '9999-99-99')"""
    return re.sub(r"[A-Za-z가-힣]", "A", re.sub(r"\d", "9", value))


def distinct_values(con, table, col):
    rows = con.execute(f"SELECT DISTINCT {col} FROM {table} LIMIT {LIST_LIMIT + 1}").fetchall()
    return [r[0] for r in rows]


def value_exists(con, tables, col, value):
    return any(
        con.execute(f"SELECT EXISTS(SELECT 1 FROM {t} WHERE {col} = ?)", (value,)).fetchone()[0]
        for t in tables
    )


def check_values(con, sql, colmap):
    issues, seen = [], set()

    equal_checks = [(c, v) for c, v in EQ_PATTERN.findall(sql)]
    for c, inner in IN_PATTERN.findall(sql):
        equal_checks += [(c, v) for v in re.findall(r"'([^']*)'", inner)]

    for col, value in equal_checks:
        key = (col.lower(), value)
        if key in seen or col.lower() not in colmap:
            continue
        seen.add(key)
        tables = colmap[col.lower()]
        if not value_exists(con, tables, col, value):
            actual = distinct_values(con, tables[0], col)
            hint = ""
            if len(actual) <= LIST_LIMIT:
                hint = f" 실제 값: {', '.join(map(str, sorted(actual, key=str)))}"
            issues.append(f"[값 존재] {col} 컬럼에 '{value}' 값이 없음.{hint}")

    range_checks = list(RANGE_PATTERN.findall(sql))
    for c, lo, hi in BETWEEN_PATTERN.findall(sql):
        range_checks += [(c, lo), (c, hi)]

    for col, value in range_checks:
        key = ("range", col.lower(), shape(value))
        if key in seen or col.lower() not in colmap:
            continue
        seen.add(key)
        table = colmap[col.lower()][0]
        sample = con.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 1").fetchone()
        if sample and shape(str(sample[0])) != shape(value):
            issues.append(
                f"[형식] {col} 조건값 '{value}'의 형식이 실제 데이터와 다름. 실제 데이터 예시: '{sample[0]}'"
            )
    return issues


def validate(sql):
    con = sqlite3.connect(DB_PATH)
    colmap = column_tables(con)
    issues, result = [], None

    try:
        rows = con.execute(sql).fetchall()
        result = rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else rows
        if not rows or result in (None, 0, []):
            issues.append(f"[결과] 결과가 비어 있거나 0·NULL임 (결과: {result}). 조건이 데이터와 맞지 않을 가능성이 있음.")
    except sqlite3.Error as e:
        issues.append(f"[실행] SQL 실행 오류: {e}")

    issues += check_values(con, sql, colmap)
    con.close()
    return result, issues


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if not DB_PATH.exists():
        sys.exit("bank_demo.db가 없습니다. 먼저 generate_mock_data.py를 실행하세요.")

    result, issues = validate(load_sql(sys.argv[1]))
    print(f"결과값: {result:,}" if isinstance(result, int) else f"결과값: {result}")

    if not issues:
        print("검증 통과: 발견된 문제 없음")
        print("(주의: 검증을 통과해도 비즈니스 정의에 맞는 답인지는 보장하지 않음)")
    else:
        print(f"검증 실패: 문제 {len(issues)}건")
        for i, msg in enumerate(issues, 1):
            print(f"  {i}. {msg}")
        print("\n--- LLM에 돌려줄 피드백 (복사해서 같은 채팅에 붙여넣기) ---")
        print("작성한 SQL을 실제 데이터베이스에서 검증한 결과 다음 문제가 발견되었습니다.")
        for msg in issues:
            print(f"- {msg}")
        print("이 정보를 반영해 SQL을 다시 작성하세요. 설명 없이 SQL만 출력하세요.")
