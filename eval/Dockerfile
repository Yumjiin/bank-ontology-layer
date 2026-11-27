FROM python:3.12-slim

WORKDIR /app
COPY eval/requirements.txt eval/requirements.txt
RUN pip install --no-cache-dir -r eval/requirements.txt

COPY eval/ eval/
COPY data/reference/semantic_definitions.yaml data/reference/semantic_definitions.yaml
ENTRYPOINT ["python", "eval/run_eval.py"]
