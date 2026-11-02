# One image, three Cloud Run roles chosen by HF_ROLE: control (stage job API, :9000), api (generated API, :8080), job (one-shot data+api run).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hf_factory ./hf_factory
COPY blueprints ./blueprints
COPY docker-entrypoint.sh .
RUN useradd -m hf && mkdir -p /data && chown hf /data
USER hf
ENV HF_RUNS_DIR=/data/runs HF_PROFILE=local PORT=8080
EXPOSE 8080
ENTRYPOINT ["./docker-entrypoint.sh"]
