"""Streamlit 데모 화면 (담당: 수민).

현재 상태: 역할 선택, Ontology Service 연결 확인, 채팅 입력만 있는 뼈대.
TODO(수민): agent.loop.run_agent 연결, 승인 대기 목록(manager), 실행 기록 패널
"""

import os
import uuid

import requests
import streamlit as st

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

st.title("Bank Ontology Layer")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if question := st.chat_input("은행 데이터에 대해 질문하세요"):
    st.session_state.messages.append({"role": "user", "content": question})
    answer = "에이전트가 아직 연결되지 않았습니다. (app/streamlit_app.py TODO)"
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()
