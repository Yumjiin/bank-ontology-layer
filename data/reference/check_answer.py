"""
정답 확인 스크립트 (BOAZ 발표 데모용)

사용법
  1) 정답과 함정별 오답 결과 보기
       python check_answer.py
  2) LLM이 만든 SQL을 파일로 저장한 뒤 정답과 비교
       python check_answer.py active sql_A_active.sql
       python check_answer.py net sql_B_net.sql

질문 키: active = 2026년 3분기 활성 고객 수, net = 2026년 9월 순입금액
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "bank_demo.db"

GOLD = {
    "active": """
        SELECT COUNT(DISTINCT r.cust_no)
        FROM txn t
        JOIN acct_rel r ON t.acct_no = r.acct_no AND r.rel_cd = '01'
        WHERE t.txn_cd IN ('IN', 'OUT')
          AND t.cncl_yn = 'N'
          AND t.txn_dt BETWEEN '20260701' AND '20260930'
    """,
    "net": """
        SELECT SUM(CASE WHEN txn_cd = 'IN' THEN amt
                        WHEN txn_cd = 'OUT' THEN -amt ELSE 0 END)
        FROM txn
        WHERE cncl_yn = 'N'
          AND txn_dt BETWEEN '20260901' AND '20260930'
    """,
}

# 정의를 모를 때 나올 법한 오답 패턴: 함정 하나씩만 어긴 SQL
TRAPS = {
    "active": [
        ("이자·수수료(시스템 거래)까지 활동으로 셈", """
            SELECT COUNT(DISTINCT r.cust_no) FROM txn t
            JOIN acct_rel r ON t.acct_no = r.acct_no AND r.rel_cd = '01'
            WHERE t.cncl_yn = 'N' AND t.txn_dt BETWEEN '20260701' AND '20260930'
        """),
        ("대리인까지 고객으로 셈", """
            SELECT COUNT(DISTINCT r.cust_no) FROM txn t
            JOIN acct_rel r ON t.acct_no = r.acct_no
            WHERE t.txn_cd IN ('IN', 'OUT') AND t.cncl_yn = 'N'
              AND t.txn_dt BETWEEN '20260701' AND '20260930'
        """),
        ("취소된 거래도 셈", """
            SELECT COUNT(DISTINCT r.cust_no) FROM txn t
            JOIN acct_rel r ON t.acct_no = r.acct_no AND r.rel_cd = '01'
            WHERE t.txn_cd IN ('IN', 'OUT')
              AND t.txn_dt BETWEEN '20260701' AND '20260930'
        """),
        ("날짜를 'YYYY-MM-DD'로 가정함", """
            SELECT COUNT(DISTINCT r.cust_no) FROM txn t
            JOIN acct_rel r ON t.acct_no = r.acct_no AND r.rel_cd = '01'
            WHERE t.txn_cd IN ('IN', 'OUT') AND t.cncl_yn = 'N'
              AND t.txn_dt BETWEEN '2026-07-01' AND '2026-09-30'
        """),
        ("정의 없이 가장 단순하게 작성함 (함정 전부 어김)", """
            SELECT COUNT(DISTINCT r.cust_no) FROM txn t
            JOIN acct_rel r ON t.acct_no = r.acct_no
            WHERE t.txn_dt BETWEEN '20260701' AND '20260930'
        """),
    ],
    "net": [
        ("취소된 거래도 셈", """
            SELECT SUM(CASE WHEN txn_cd = 'IN' THEN amt
                            WHEN txn_cd = 'OUT' THEN -amt ELSE 0 END)
            FROM txn WHERE txn_dt BETWEEN '20260901' AND '20260930'
        """),
        ("이자·수수료까지 입출금으로 셈", """
            SELECT SUM(CASE WHEN txn_cd IN ('IN', 'INT') THEN amt
                            WHEN txn_cd IN ('OUT', 'FEE') THEN -amt ELSE 0 END)
            FROM txn WHERE cncl_yn = 'N' AND txn_dt BETWEEN '20260901' AND '20260930'
        """),
        ("금액 방향을 무시하고 모두 더함", """
            SELECT SUM(amt) FROM txn
            WHERE cncl_yn = 'N' AND txn_dt BETWEEN '20260901' AND '20260930'
        """),
        ("날짜를 'YYYY-MM-DD'로 가정함", """
            SELECT SUM(CASE WHEN txn_cd = 'IN' THEN amt
                            WHEN txn_cd = 'OUT' THEN -amt ELSE 0 END)
            FROM txn WHERE cncl_yn = 'N' AND txn_dt BETWEEN '2026-09-01' AND '2026-09-30'
        """),
    ],
}

LABELS = {"active": "2026년 3분기 활성 고객 수", "net": "2026년 9월 순입금액"}


def run(con, sql):
    rows = con.execute(sql).fetchall()
    return rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else rows


def fmt(v):
    return f"{v:,}" if isinstance(v, int) else str(v)


def show_overview(con):
    for key in GOLD:
        gold = run(con, GOLD[key])
        print(f"\n[{LABELS[key]}]")
        print(f"  정답: {fmt(gold)}")
        for desc, sql in TRAPS[key]:
            value = run(con, sql)
            mark = "같음" if value == gold else "틀림"
            print(f"  - {desc}: {fmt(value)} ({mark})")


def compare(con, key, sql_path):
    sql = Path(sql_path).read_text(encoding="utf-8").strip().strip("`")
    if sql.lower().startswith("sql"):
        sql = sql[3:]
    gold = run(con, GOLD[key])
    try:
        value = run(con, sql)
    except sqlite3.Error as e:
        print(f"SQL 실행 오류: {e}")
        return
    print(f"[{LABELS[key]}]")
    print(f"  LLM 결과: {fmt(value)}")
    print(f"  정답:     {fmt(gold)}")
    print("  판정:     " + ("정답" if value == gold else "오답"))


if __name__ == "__main__":
    if not DB_PATH.exists():
        sys.exit("bank_demo.db가 없습니다. 먼저 generate_mock_data.py를 실행하세요.")
    con = sqlite3.connect(DB_PATH)
    if len(sys.argv) == 1:
        show_overview(con)
    elif len(sys.argv) == 3 and sys.argv[1] in GOLD:
        compare(con, sys.argv[1], sys.argv[2])
    else:
        print(__doc__)
    con.close()
