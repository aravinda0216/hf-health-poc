PY ?= python3
# Install dependencies (see requirements.txt for the minimum versions).
install:      ; pip install -U -r requirements.txt   # add --break-system-packages on a system Python; use a venv if you can
# Dev-scale run (1,000 members): blueprint -> data engine -> API engine -> smoke test -> delivery manifest in out/dev.
demo:         ; $(PY) -m hf_factory.run --members 1000 --out out/dev        # dev scale
# Blueprint-scale run (50,000 members) into out/full.
demo-full:    ; $(PY) -m hf_factory.run --out out/full                       # blueprint scale (50,000 members)
# Contract tests and data tests (generates a small dataset unless HF_TEST_DB is set).
test:         ; $(PY) -m pytest tests -q -p no:cacheprovider -W ignore
# Repeatability checks: replay must match, re-seed must differ, evolve must keep earlier rows unchanged.
repro:        ; $(PY) -m hf_factory.repro --members 1000
# Start the API on :8080 against the dev database (fault injection on; HF_FAULT=off disables it).
serve:        ; HF_DB=out/dev/factory.db $(PY) -m uvicorn hf_factory.api.app:from_env --factory --port 8080
