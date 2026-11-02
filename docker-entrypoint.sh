#!/bin/sh
# HF_ROLE=control | api | job. Cloud Run supplies $PORT.
set -e
case "${HF_ROLE:-api}" in
  control) exec python -m uvicorn hf_factory.control.app:from_env --factory --host 0.0.0.0 --port "${PORT:-9000}" ;;
  api)     : "${HF_DB:?HF_DB must point at a generated dataset}"; exec python -m uvicorn hf_factory.api.app:from_env --factory --host 0.0.0.0 --port "${PORT:-8080}" ;;
  job)     exec python -m hf_factory.run --blueprint "${HF_BLUEPRINT:-blueprints/s1_provider_demo.json}" --members "${HF_MEMBERS:-1000}" --out /data/out ;;
  *) echo "unknown HF_ROLE" >&2; exit 2 ;;
esac
