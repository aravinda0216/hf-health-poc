# One image, four roles chosen by HF_ROLE:
#   api      generated API (default, :$PORT)                 needs HF_DB (file) or HF_DATASET_URI (gs://...) with HF_PROFILE=gcp
#   control  stage-job control plane for the orchestrator (:$PORT)
#   job      one-shot run: blueprint -> data engine -> API engine -> smoke tests, output in /data/out
#   publish  push a dataset to GCS and its knowledge chunks to Vertex AI Vector Search (python -m hf_factory.gcp_publish "$@")
#
#   docker build -t hf-factory .                                 # local / in-memory profiles
#   docker build --build-arg INSTALL_GCP=1 -t hf-factory-gcp .   # adds the Google Cloud client libraries for HF_PROFILE=gcp / HF_SEARCH=vertex
FROM python:3.12-slim
ARG INSTALL_GCP=0
WORKDIR /app
COPY requirements.txt requirements-gcp.txt ./
RUN pip install --no-cache-dir -r requirements.txt && if [ "$INSTALL_GCP" = "1" ]; then pip install --no-cache-dir -r requirements-gcp.txt; fi
COPY hf_factory ./hf_factory
COPY blueprints ./blueprints
COPY docker-entrypoint.sh .
RUN useradd -m hf && mkdir -p /data && chown hf /data
USER hf
ENV HF_RUNS_DIR=/data/runs HF_PROFILE=local PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=2)" || exit 1
ENTRYPOINT ["./docker-entrypoint.sh"]
