#!/bin/sh
# HF_ROLE=api | control | job | publish. Cloud Run supplies $PORT.
set -e
case "${HF_ROLE:-api}" in
  control) exec python -m uvicorn hf_factory.control.app:from_env --factory --host 0.0.0.0 --port "${PORT:-9000}" ;;
  api)
    if [ -z "$HF_DB" ] && [ -z "$HF_DATASET_URI" ]; then echo "set HF_DB (dataset file) or HF_DATASET_URI (gs://...)" >&2; exit 2; fi
    exec python -m uvicorn hf_factory.api.app:from_env --factory --host 0.0.0.0 --port "${PORT:-8080}" ;;
  job)     exec python -m hf_factory.run --blueprint "${HF_BLUEPRINT:-blueprints/s1_provider_demo.json}" --members "${HF_MEMBERS:-1000}" --out /data/out ;;
  publish) exec python -m hf_factory.gcp_publish "$@" ;;
  *) echo "unknown HF_ROLE (api | control | job | publish)" >&2; exit 2 ;;
esac
