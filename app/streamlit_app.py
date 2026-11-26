"""Streamlit 데모 화면 (담당: 수민).

TODO(수민): 승인 대기 목록(manager), 실행 기록 패널
"""

import os
import uuid

import requests
import streamlit as st

from agent.loop import run_agent

API_URL = os.environ.get("ONTOLOGY_API_URL", "http://localhost:8001")
ROLE_LABELS = {"teller": "상담원", "marketer": "마케팅", "manager": "관리자"}

st.set_page_config(page_title="Bank Ontology Layer", layout="wide")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("설정")
    role = st.selectbox(
        "역할",
        list(ROLE_LABELS),
        format_func=ROLE_LABELS.__getitem__,
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
        if "agent_role" in msg:
            caption = f"역할: {ROLE_LABELS[msg['agent_role']]}"
            if "tools" in msg:
                tools = " → ".join(msg["tools"]) or "없음"
                caption += f" · 사용한 도구: {tools}"
            st.caption(caption)

if question := st.chat_input("은행 데이터에 대해 질문하세요"):
    st.session_state.messages.append({"role": "user", "content": question})
    reply = {"role": "assistant", "agent_role": role}
    with st.spinner("답변을 만드는 중입니다…"):
        try:
            result = run_agent(question, role, st.session_state.session_id)
            answer = result["answer"]
            reply["tools"] = [step["tool"] for step in result["steps"]]
            if not result["trace_saved"]:
                answer += "\n\n일부 실행 기록을 저장하지 못했습니다."
        except requests.RequestException:
            answer = "모델 호출에 실패했습니다. 잠시 후 다시 시도해 주세요."
        except ValueError:
            answer = "모델 응답을 처리하지 못했습니다. LLM 설정을 확인해 주세요."
    reply["content"] = answer
    st.session_state.messages.append(reply)
    st.rerun()
