# 빌드 컨텍스트는 저장소 루트 (agent 모듈을 함께 쓰기 위함)
FROM python:3.12-slim

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY app/ ./app/

ENV PYTHONPATH=/app
EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
