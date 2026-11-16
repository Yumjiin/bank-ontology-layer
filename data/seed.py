"""DB 스키마 생성과 목데이터 적재.

docker compose up 시 seed 컨테이너에서 한 번 실행되고 종료됨.
- schema.sql 실행 (IF NOT EXISTS라 재실행해도 안전함)
- 원천 테이블이 비어 있을 때만 목데이터를 적재함 (중복 적재 방지)

TODO(지영):
- 발표 데모 generate_mock_data.py를 이 폴더로 가져와 PostgreSQL에 적재하도록 수정
- load_mock_data()에서 그 함수를 호출하도록 연결
"""

import os
import sys
from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


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


def load_mock_data(conn: psycopg.Connection) -> bool:
    """목데이터를 적재하고 적재 여부를 반환함. TODO(지영): 구현."""
    return False


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
            print("[seed] 목데이터 적재가 아직 구현되지 않음 (data/seed.py TODO)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
