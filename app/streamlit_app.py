"""Streamlit 데모 화면 (담당: 수민).

TODO(수민): 승인 대기 목록(manager), 실행 기록 패널
"""

import os
import uuid

import requests
import streamlit as st

from agent.loop import run_agent

API_URL = os.environ.get("ONTOLOGY_API_URL", "http://localhost:8001")

st.set_page_config(page_title="Bank Ontology Layer", layout="wide")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("설정")
    role = st.selectbox(
        "역할",
        ["teller", "marketer", "manager"],
        format_func=lambda r: {"teller": "상담원", "marketer": "마케팅", "manager": "관리자"}[r],
    )
    if st.button("세션 초기화"):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []

    try:
        ok = requests.get(f"{API_URL}/health", timeout=3).ok
    except requests.RequestException:
        ok = False
    st.caption(f"Ontology Service: {'연결됨' if ok else '연결 안 됨'}")
    st.caption("개발 모드 · 조회와 승인 요청은 예시 응답을 사용합니다.")

st.title("Bank Ontology Layer")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if question := st.chat_input("은행 데이터에 대해 질문하세요"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.spinner("답변을 만드는 중입니다…"):
        try:
            answer = run_agent(question, role, st.session_state.session_id)["answer"]
        except requests.RequestException:
            answer = "모델 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
        except ValueError:
            answer = "모델 응답을 처리하지 못했습니다. LLM 설정을 확인해 주세요."
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()
