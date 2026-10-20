"""Stage s8 artifacts: OpenAPI 3.1 file, agent tool manifest, contract-test run, sample calls."""
import json, os, subprocess, sys, yaml

# Agents cannot follow '#/components' references, so each tool's schema is inlined recursively (and Optional[X] is flattened to X).
def resolve(spec, node):
    """Fully inline $ref / Optional[...] so each tool's input_schema is self-contained (agents cannot follow #/components refs)."""
    if isinstance(node, list): return [resolve(spec, x) for x in node]
    if not isinstance(node, dict): return node
    if "$ref" in node: return resolve(spec, spec["components"]["schemas"][node["$ref"].split("/")[-1]])
    if "anyOf" in node:
        non_null = [n for n in node["anyOf"] if n.get("type") != "null"]
        if len(non_null) == 1:
            base = resolve(spec, non_null[0]); extra = {k: v for k, v in node.items() if k not in ("anyOf", "title")}
            return {**base, **extra}
    return {k: resolve(spec, v) for k, v in node.items() if k != "title"}

# Build the agent tool list from the OpenAPI spec: one tool per business endpoint, with parameters merged with body fields,
# the route's x-agent-tool guardrails (escalate/never), and the HTTP binding. The token endpoint is excluded.
def tool_manifest(spec):
    tools = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if op["operationId"] == "createAccessToken": continue
            props, req = {}, []
            for p in op.get("parameters", []):
                s = dict(resolve(spec, p["schema"])); s["description"] = p.get("description") or p["name"]; s["in"] = p["in"]; props[p["name"]] = {k: v for k, v in s.items() if k not in ("title",)}
                if p.get("required"): req.append(p["name"])
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                b = resolve(spec, body)
                for k, v in b.get("properties", {}).items(): props[k] = {**{kk: vv for kk, vv in resolve(spec, v).items() if kk != "title"}, "in": "body"}
                req += b.get("required", [])
            g = op.get("x-agent-tool", {})
            tools.append({"name": op["operationId"], "description": op["summary"] + (" " + g["when"] if g.get("when") else ""), "http": {"method": method.upper(), "path": path},
                          "input_schema": {"type": "object", "properties": props, "required": sorted(set(req))}, "auth": "OAuth2 client credentials (Bearer)",
                          "guardrails": {"escalate_to_human": g.get("escalate", []), "never": g.get("never", [])}})
    return {"manifest_version": "1.0", "tools": tools, "note": "Bind to Dialogflow CX webhooks / Vertex AI agent tools in the infra engine (s9)."}

# Write openapi.json/.yaml and tools.json from the live app, so the published contract can never drift from the code.
def write_artifacts(app, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    spec = app.openapi()
    json.dump(spec, open(os.path.join(out_dir, "openapi.json"), "w"), indent=1)
    yaml.safe_dump(spec, open(os.path.join(out_dir, "openapi.yaml"), "w"), sort_keys=False)
    tm = tool_manifest(spec); json.dump(tm, open(os.path.join(out_dir, "tools.json"), "w"), indent=1)
    return spec, tm

# Run the contract test files against the generated database in a subprocess. The orchestrator stops if any test fails.
def run_contract_tests(db_path):
    env = dict(os.environ, HF_TEST_DB=os.path.abspath(db_path))
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/test_contract.py", "tests/test_contract_provider.py", "-q", "--no-header", "-p", "no:cacheprovider", "-W", "ignore"], capture_output=True, text=True, env=env)
    last = [l for l in p.stdout.strip().splitlines() if l.strip()][-1] if p.stdout.strip() else p.stderr[-300:]
    import re
    passed = int((re.search(r"(\d+) passed", last) or [0, 0])[1]); failed = int((re.search(r"(\d+) failed", last) or [0, 0])[1])
    return dict(total=passed + failed, passed=passed, failed=failed, summary=last, output=("" if failed == 0 else p.stdout[-2000:]))
