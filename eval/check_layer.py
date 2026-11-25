"""온톨로지 레이어 정답 확인: 평가 질문셋을 '조건 C의 이상적인 도구 호출'로 실행해 정답과 대조함.

LLM 없이 사람이 고른 도구 호출로 레이어(서비스) 자체가 정답을 낼 수 있는지 확인하는 용도.
- 여기서 맞고 조건 C 평가에서 틀린 문항 → LLM의 도구 선택·인자 문제
- 여기서도 틀린 문항 → 온톨로지 정의나 서비스 문제
새 문항을 추가하면 CALLS에도 같은 id로 도구 호출을 추가함.

실행 (레포 루트, service/requirements.txt 설치된 환경, DB는 docker compose로 실행 중)
  DATABASE_URL=postgresql://bank:bank@localhost:5433/bank python eval/check_layer.py
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))

import query  # noqa: E402
from metadata import load_ontology  # noqa: E402
from schemas import Filter, LinkRequest, MetricRequest, Period, SearchRequest  # noqa: E402

onto = load_ontology(ROOT / "ontology" / "ontology.yaml")
eng = query.make_engine()  # DATABASE_URL 환경변수, 없으면 localhost:5433

F = lambda p, op, v: Filter(property=p, op=op, value=v)

def metric(name, s, e, *fs):
    return query.get_metric(eng, onto, name, MetricRequest(period=Period(start=s, end=e), filters=list(fs)))["value"]
def total(obj, *fs):
    return query.search_objects(eng, onto, SearchRequest(object_type=obj, filters=list(fs), limit=1))["total"]
def first(obj, prop, *fs):
    return query.search_objects(eng, onto, SearchRequest(object_type=obj, filters=list(fs), properties=[prop], limit=1))["items"][0][prop]
def link(obj, oid, ln, *fs, prop=None):
    r = query.get_linked_objects(eng, onto, obj, oid, ln, LinkRequest(filters=list(fs), limit=20))
    return r["items"][0][prop] if prop else r["total"]

CALLS = {
    "q01": lambda: metric("active_customers", "2026-07-01", "2026-09-30"),
    "q02": lambda: metric("net_deposit", "2026-09-01", "2026-09-30"),
    "q03": lambda: metric("active_customers", "2026-01-01", "2026-06-30"),
    "q04": lambda: metric("active_customers", "2026-07-01", "2026-07-31"),
    "q05": lambda: metric("net_deposit", "2026-08-01", "2026-08-31"),
    "q06": lambda: metric("net_deposit", "2026-01-01", "2026-03-31"),
    "q07": lambda: metric("net_deposit", "2026-07-01", "2026-09-30", F("channel", "eq", "모바일뱅킹")),
    "q08": lambda: metric("net_deposit", "2026-09-01", "2026-09-30", F("channel", "eq", "창구")),
    "q09": lambda: total("Customer", F("sex", "eq", "여성")),
    "q10": lambda: total("Customer", F("join_date", "between", ["2020-01-01", "2020-12-31"])),
    "q11": lambda: total("Customer", F("birth_date", "between", ["1990-01-01", "1999-12-31"])),
    "q12": lambda: total("Customer", F("grade", "eq", "VIP")),
    "q13": lambda: total("Account", F("status", "eq", "휴면")),
    "q14": lambda: total("Account", F("status", "eq", "해지")),
    "q15": lambda: total("Account", F("product", "eq", "정기적금")),
    "q16": lambda: total("Account", F("open_date", "between", ["2020-01-01", "2020-12-31"])),
    "q17": lambda: total("Transaction", F("cancelled", "eq", "true"), F("date", "between", ["2026-09-01", "2026-09-30"])),
    "q18": lambda: total("Branch", F("region", "eq", "서울")),
    "q19": lambda: total("Branch", F("staff_count", "gte", 20)),
    "q20": lambda: total("Branch", F("region", "eq", "인천")),
    "q21": lambda: link("Customer", "C00003", "owns"),
    "q22": lambda: link("Customer", "C00086", "delegated", prop="acct_no"),
    "q23": lambda: link("Account", "110-000001", "has_transaction", F("type", "eq", "입금"), F("date", "between", ["2026-09-01", "2026-09-30"])),
    "q24": lambda: link("Account", "110-000001", "managed_by", prop="name"),
    "q25": lambda: total("Branch"),
    "q26": lambda: first("Account", "branch_cd", F("acct_no", "eq", "110-000001")),
    "q27": lambda: first("Customer", "name", F("cust_no", "eq", "C00001")),
}
qs = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"]
bad = 0
for q in qs:
    try:
        got = CALLS[q["id"]]()
    except Exception as e:
        got = f"ERROR {type(e).__name__}: {e}"
    ok = str(got) == str(q["answer"])
    bad += not ok
    print(f"{'OK' if ok else 'XX'} {q['id']} {q['tags'][0]:<6} 정답={q['answer']!s:<11} 결과={got!s:<11} {q['question']}")
print(f"\n불일치 {bad} / {len(qs)}")
sys.exit(1 if bad else 0)
