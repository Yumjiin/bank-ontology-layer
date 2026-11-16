"""
은행 목데이터 생성 스크립트 (BOAZ 발표 데모용)

실행:   python generate_mock_data.py
결과:   bank_demo.db (SQLite), prompts/ 폴더의 프롬프트 4개

외부 라이브러리 없이 파이썬 표준 라이브러리만 사용함.
SEED가 고정되어 있어 누가 실행해도 같은 데이터가 만들어짐.
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 42
BASE = Path(__file__).parent
DB_PATH = BASE / "bank_demo.db"
PROMPT_DIR = BASE / "prompts"
YAML_PATH = BASE / "semantic_definitions.yaml"

START = date(2026, 1, 1)
END = date(2026, 9, 30)
Q3_START = date(2026, 7, 1)

QUESTIONS = {
    "active": "2026년 3분기(7~9월) 활성 고객 수는?",
    "net": "2026년 9월 순입금액은?",
}

# 테이블 정의: 컬럼 설명 주석이 없는 것이 실제 현업 DB와 비슷한 상황임
DDL = """CREATE TABLE brnc (
    brnc_cd TEXT PRIMARY KEY,
    attr1 TEXT,
    attr2 TEXT,
    attr3 TEXT,
    attr4 INTEGER,
    attr5 TEXT
);
CREATE TABLE cust (
    cust_no TEXT PRIMARY KEY,
    cust_nm TEXT,
    birth_ymd TEXT,
    sex_cd TEXT,
    join_ymd TEXT
);
CREATE TABLE acct (
    acct_no TEXT PRIMARY KEY,
    prod_cd TEXT,
    open_ymd TEXT,
    acct_stat_cd TEXT,
    brnc_cd TEXT REFERENCES brnc(brnc_cd)
);
CREATE TABLE acct_rel (
    acct_no TEXT REFERENCES acct(acct_no),
    cust_no TEXT REFERENCES cust(cust_no),
    rel_cd TEXT,
    PRIMARY KEY (acct_no, cust_no)
);
CREATE TABLE txn (
    txn_id INTEGER PRIMARY KEY,
    acct_no TEXT REFERENCES acct(acct_no),
    txn_dt TEXT,
    txn_tm TEXT,
    txn_cd TEXT,
    amt INTEGER,
    cncl_yn TEXT,
    chnl_cd TEXT
);"""

LAST_NAMES = list("김이박최정강조윤장임한오서신권황안송류홍")
FIRST_NAMES = [
    "민준", "서연", "도윤", "지우", "하준", "서윤", "시우", "지민", "주원", "하은",
    "지호", "수아", "준서", "지아", "예준", "채원", "건우", "다은", "현우", "유진",
]
BRANCHES = [
    ("B001", "강남지점", "11", "19980302", 24, "01"),
    ("B002", "여의도지점", "11", "20010515", 31, "01"),
    ("B003", "종로지점", "11", "19951120", 18, "01"),
    ("B004", "송도지점", "28", "20120903", 15, "01"),
    ("B005", "부평지점", "28", "20050411", 12, "01"),
    ("B006", "판교지점", "41", "20140707", 20, "01"),
    ("B007", "수원지점", "41", "20030224", 16, "01"),
    ("B008", "인천공항출장소", "28", "20180601", 4, "02"),
]


def ymd(d):
    return d.strftime("%Y%m%d")


def rand_date(a, b):
    return a + timedelta(days=random.randint(0, (b - a).days))


def rand_time():
    return f"{random.randint(8, 22):02d}{random.randint(0, 59):02d}{random.randint(0, 59):02d}"


def month_starts(a, b):
    d = date(a.year, a.month, 1)
    while d <= b:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def month_end(d):
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


class Builder:
    def __init__(self):
        self.custs, self.accts, self.rels, self.txns = [], [], [], []
        self.acct_seq = 0

    def add_cust(self, i):
        cust_no = f"C{i:05d}"
        self.custs.append((
            cust_no,
            random.choice(LAST_NAMES) + random.choice(FIRST_NAMES),
            ymd(rand_date(date(1960, 1, 1), date(2004, 12, 31))),
            random.choice(["1", "2"]),
            ymd(rand_date(date(2010, 1, 1), date(2025, 12, 31))),
        ))
        return cust_no

    def add_acct(self, owner, prod_cd="DD01", stat="A"):
        self.acct_seq += 1
        acct_no = f"110-{self.acct_seq:06d}"
        self.accts.append((
            acct_no, prod_cd, ymd(rand_date(date(2012, 1, 1), date(2025, 12, 31))),
            stat, random.choice(BRANCHES)[0],
        ))
        self.rels.append((acct_no, owner, "01"))
        return acct_no

    def add_txn(self, acct_no, d, code, amt, cncl="N"):
        chnl = random.choice(["01", "02", "03", "04", "04", "04"]) if code in ("IN", "OUT") else "03"
        self.txns.append([acct_no, ymd(d), rand_time(), code, amt, cncl, chnl])

    def customer_txns(self, acct_no, start, end, per_month=(2, 8), cancel_rate=0.03):
        for m in month_starts(start, end):
            lo, hi = max(m, start), min(month_end(m), end)
            for _ in range(random.randint(*per_month)):
                if random.random() < 0.45:
                    code, amt = "IN", random.randint(50, 4000) * 1000
                else:
                    code, amt = "OUT", random.randint(10, 1500) * 1000
                cncl = "Y" if random.random() < cancel_rate else "N"
                self.add_txn(acct_no, rand_date(lo, hi), code, amt, cncl)

    def system_txns(self, acct_no, start, end, fee_rate=0.3):
        # 매월 25일 이자 지급, 일부 계좌는 수수료 발생
        for m in month_starts(start, end):
            pay_day = date(m.year, m.month, 25)
            if start <= pay_day <= end:
                self.add_txn(acct_no, pay_day, "INT", random.randint(10, 9000))
                if random.random() < fee_rate:
                    self.add_txn(acct_no, pay_day, "FEE", random.choice([500, 1000, 2000]))


def build():
    random.seed(SEED)
    b = Builder()
    active_accts = []

    profiles = (
        ["active"] * 165 + ["lapsed"] * 35 + ["cancel_only"] * 12
        + ["dormant"] * 36 + ["closed"] * 22 + ["proxy_only"] * 30
    )
    random.shuffle(profiles)
    proxy_custs, spouse_candidates = [], []

    for i, profile in enumerate(profiles, start=1):
        cust = b.add_cust(i)

        if profile == "active":
            # 3분기까지 꾸준히 입출금하는 고객
            acct = b.add_acct(cust)
            active_accts.append(acct)
            b.customer_txns(acct, START, END)
            b.system_txns(acct, START, END)
            if random.random() < 0.25:
                sv = b.add_acct(cust, prod_cd="SV01")
                for m in month_starts(START, END):
                    b.add_txn(sv, date(m.year, m.month, 10), "IN", 300000)
                b.system_txns(sv, START, END, fee_rate=0)

        elif profile in ("lapsed", "cancel_only"):
            # 상반기까지만 거래하고 3분기에는 이자·수수료만 발생
            acct = b.add_acct(cust)
            b.customer_txns(acct, START, date(2026, 6, 30))
            b.system_txns(acct, START, END)
            if profile == "cancel_only":
                # 3분기 고객 거래가 딱 1건 있지만 취소된 거래
                b.add_txn(acct, rand_date(Q3_START, END), "OUT", random.randint(10, 500) * 1000, cncl="Y")
            spouse_candidates.append(cust)

        elif profile == "dormant":
            # 휴면 계좌: 이자만 발생
            acct = b.add_acct(cust, stat="D")
            b.system_txns(acct, START, END, fee_rate=0)
            spouse_candidates.append(cust)

        elif profile == "closed":
            # 상반기에 해지된 계좌
            close_day = rand_date(date(2026, 2, 1), date(2026, 5, 31))
            acct = b.add_acct(cust, stat="C")
            b.customer_txns(acct, START, close_day)
            b.system_txns(acct, START, close_day)

        else:  # proxy_only: 본인 계좌 없이 다른 고객 계좌의 대리인으로만 등록됨
            proxy_custs.append(cust)

    # 대리인 연결: 활동 고객 계좌에 대리인을 붙임 (명의인만 세지 않으면 중복 집계됨)
    targets = random.sample(active_accts, len(proxy_custs) + 15)
    for acct, cust in zip(targets, proxy_custs + random.sample(spouse_candidates, 15)):
        b.rels.append((acct, cust, "02"))

    b.txns.sort(key=lambda t: (t[1], t[2]))
    return b


def write_db(b):
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.executescript(DDL)
    con.executemany("INSERT INTO brnc VALUES (?,?,?,?,?,?)", BRANCHES)
    con.executemany("INSERT INTO cust VALUES (?,?,?,?,?)", b.custs)
    con.executemany("INSERT INTO acct VALUES (?,?,?,?,?)", b.accts)
    con.executemany("INSERT INTO acct_rel VALUES (?,?,?)", b.rels)
    con.executemany(
        "INSERT INTO txn (acct_no, txn_dt, txn_tm, txn_cd, amt, cncl_yn, chnl_cd) VALUES (?,?,?,?,?,?,?)",
        b.txns,
    )
    con.commit()
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("brnc", "cust", "acct", "acct_rel", "txn")}
    con.close()
    return counts


def write_prompts():
    PROMPT_DIR.mkdir(exist_ok=True)
    definitions = YAML_PATH.read_text(encoding="utf-8")
    tail = "위 질문에 답하는 SQLite SQL 쿼리 하나만 작성하세요. 설명 없이 SQL만 출력하세요."

    for key, question in QUESTIONS.items():
        prompt_a = (
            "다음은 은행 데이터베이스(SQLite)의 스키마입니다.\n\n"
            f"{DDL}\n\n질문: {question}\n\n{tail}\n"
        )
        prompt_b = (
            "다음은 은행 데이터베이스(SQLite)의 스키마입니다.\n\n"
            f"{DDL}\n\n"
            "다음은 이 데이터베이스의 데이터 정의 문서입니다.\n\n"
            f"{definitions}\n"
            f"질문: {question}\n\n{tail}\n"
        )
        (PROMPT_DIR / f"prompt_A_{key}.txt").write_text(prompt_a, encoding="utf-8")
        (PROMPT_DIR / f"prompt_B_{key}.txt").write_text(prompt_b, encoding="utf-8")


if __name__ == "__main__":
    counts = write_db(build())
    write_prompts()
    print(f"생성 완료: {DB_PATH.name}")
    for table, n in counts.items():
        print(f"  {table:<9} {n:>6}행")
    print(f"프롬프트: {PROMPT_DIR.name}/ 폴더에 4개 생성")
