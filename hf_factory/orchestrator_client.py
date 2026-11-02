"""Minimal orchestrator client: drives a running control plane through s3..s8 for a blueprint (what the real orchestrator does with its stage job specs).
  python -m hf_factory.orchestrator_client --url http://127.0.0.1:9000 --token $HF_CONTROL_TOKEN --members 1000"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from . import blueprint

def call(url, token, method, path, body=None):
    req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as r: return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}"), dict(e.headers)

def drive(url, token, bp, run_id=None, log=print):
    """Post s3..s7 then s8; retry a stage up to 2 times on 5xx (the control plane replays earlier stages deterministically). Stops at the first non-200."""
    run_id = run_id or "run_" + bp["blueprint_hash"][7:19]
    for st in blueprint.STAGES:
        for attempt in range(3):
            code, body, _ = call(url, token, "POST", f"/factory/data-engine/{st['id']}", blueprint.job_spec(bp, st, [], run_id=run_id))
            if code < 500: break
            log(f"  {st['id']} attempt {attempt+1} failed: {body.get('error', {}).get('message')}")
        log(f"[{st['id']}] {code} {body.get('status') or body.get('error', {}).get('code')}  {body.get('duration_s', '')}s")
        if code != 200 or body.get("status") != "COMPLETED": return run_id, code, body
    code, body, _ = call(url, token, "POST", "/factory/api-engine/s8", blueprint.api_job_spec(bp, run_id))
    log(f"[s8] {code} {body.get('status') or body.get('error', {}).get('code')}  endpoints={body.get('endpoint_count')} contract_tests={body.get('contract_tests', {}).get('passed')}/{body.get('contract_tests', {}).get('total')}")
    return run_id, code, body

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--url", default="http://127.0.0.1:9000"); ap.add_argument("--token", default=os.environ.get("HF_CONTROL_TOKEN", "demo-control-token-change-me"))
    ap.add_argument("--blueprint", default="blueprints/s1_provider_demo.json"); ap.add_argument("--members", type=int); ap.add_argument("--seed", type=int); ap.add_argument("--run-id")
    a = ap.parse_args(argv); bp = blueprint.load(a.blueprint, members=a.members, seed=a.seed)
    run_id, code, body = drive(a.url, a.token, bp, a.run_id)
    if code != 200 or body.get("status") != "DEPLOYED": print(json.dumps(body, indent=1)[:1500]); sys.exit(1)
    print("run:", run_id, "->", json.dumps(call(a.url, a.token, "GET", f"/factory/runs/{run_id}")[1]["state"]))

if __name__ == "__main__": main()
