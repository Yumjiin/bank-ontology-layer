"""DB 스키마 생성과 목데이터 적재.

docker compose up 시 seed 컨테이너에서 한 번 실행되고 종료됨.
- schema.sql 실행 (IF NOT EXISTS라 재실행해도 안전함)
- 원천 테이블이 비어 있을 때만 목데이터를 적재함 (중복 적재 방지)

목데이터는 발표 데모의 reference/generate_mock_data.py의 build()를 그대로 재사용함.
생성 로직을 고치지 않으므로 SEED 고정으로 같은 데이터가 만들어지고,
정답값(3분기 활성 고객 165명, 9월 순입금액 389,638,000원)도 그대로 유지됨.

고객 등급(cust_grd_cd)은 build()가 끝난 뒤 규칙으로 계산해 붙임.
난수를 쓰지 않으므로 기존 데이터에는 영향이 없음. 규칙은 assign_grades() 참고.
"""

import os
import sys
from pathlib import Path

import psycopg

from reference.generate_mock_data import BRANCHES, build

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 고객 등급 규칙: 상반기(1~6월) 명의인 계좌의 고객 입금(IN, 취소 제외) 합계 기준
GRADE_PERIOD = ("20260101", "20260630")
VIP_THRESHOLD = 30_000_000
GRADE_VIP, GRADE_NORMAL = "01", "02"


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "postgresql://bank:bank@db:5432/bank")
    # SQLAlchemy 형식(postgresql+psycopg://)이 들어와도 동작하게 함
    return dsn.replace("postgresql+psycopg://", "postgresql://")


def table_exists(conn: psycopg.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = %s)",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def already_loaded(conn: psycopg.Connection) -> bool:
    if not table_exists(conn, "cust"):
        return False
    row = conn.execute("SELECT COUNT(*) FROM cust").fetchone()
    return bool(row and row[0] > 0)


def assign_grades(b) -> dict[str, str]:
    """고객번호 -> 등급코드. 원본 데이터에 없던 등급을 규칙으로 계산함.

    규칙: 상반기(1~6월) 명의인 계좌의 고객 입금(IN) 합계가 3,000만 원 이상이면 VIP(01), 나머지는 일반(02)
    - 기존 정의(명의인 rel_cd='01', 고객 거래 IN, 취소 cncl_yn='Y' 제외)만 사용해 새 함정을 만들지 않음
    - 기간을 상반기로 둔 이유: "상반기 VIP 중 3분기 거래가 끊긴 고객" 같은 이탈 분석 질문이 성립하도록 함
    - 결과: VIP 65명, 그중 3분기 고객 거래가 없는 고객 9명
    """
    owner = {acct_no: cust_no for acct_no, cust_no, rel_cd in b.rels if rel_cd == "01"}
    start, end = GRADE_PERIOD
    deposit: dict[str, int] = {}
    for acct_no, txn_dt, _tm, txn_cd, amt, cncl_yn, _chnl in b.txns:
        if txn_cd == "IN" and cncl_yn == "N" and start <= txn_dt <= end:
            cust_no = owner[acct_no]
            deposit[cust_no] = deposit.get(cust_no, 0) + amt
    return {
        cust_no: GRADE_VIP if deposit.get(cust_no, 0) >= VIP_THRESHOLD else GRADE_NORMAL
        for cust_no, *_ in b.custs
    }


def load_mock_data(conn: psycopg.Connection) -> bool:
    """목데이터를 생성해 원천 테이블 5개에 적재함.

    - 적재 순서는 FK 순서(brnc -> cust -> acct -> acct_rel -> txn)
    - 전체를 하나의 트랜잭션으로 묶음. 중간에 실패하면 모두 롤백되어,
      일부만 적재된 상태로 남아 already_loaded()가 적재 완료로 오판하는 일을 막음
    """
    b = build()
    grades = assign_grades(b)
    custs = [(*row, grades[row[0]]) for row in b.custs]
    # 원본(SQLite)은 txn_id를 정렬된 순서대로 1부터 자동 부여함. 같은 번호를 직접 넣어 원본과 일치시킴
    txns = [(i, *row) for i, row in enumerate(b.txns, start=1)]

    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO brnc VALUES (%s, %s, %s, %s, %s, %s)", BRANCHES)
        cur.executemany("INSERT INTO cust VALUES (%s, %s, %s, %s, %s, %s)", custs)
        cur.executemany("INSERT INTO acct VALUES (%s, %s, %s, %s, %s)", b.accts)
        cur.executemany("INSERT INTO acct_rel VALUES (%s, %s, %s)", b.rels)
        cur.executemany("INSERT INTO txn VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", txns)

    for table in ("brnc", "cust", "acct", "acct_rel", "txn"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"[seed]   {table:<9} {n:>6}행")
    return True


def main() -> int:
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        print("[seed] schema.sql 적용 완료")

        if already_loaded(conn):
            print("[seed] 목데이터가 이미 있어 적재를 건너뜀")
            return 0

        if load_mock_data(conn):
            print("[seed] 목데이터 적재 완료")
        else:
            print("[seed] 목데이터 적재 실패")
    return 0


if __name__ == "__main__":
    sys.exit(main())
