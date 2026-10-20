import os, sqlite3, tempfile, warnings
import pytest
warnings.filterwarnings("ignore")
from fastapi.testclient import TestClient
from hf_factory import blueprint, data_engine
from hf_factory.api.app import create_app, Config

@pytest.fixture(scope="session")
# Dataset under test. `make demo` sets HF_TEST_DB so the contract tests run against the generated database; run alone, a 400-member dataset is generated once per session.
def db_path():
    p = os.environ.get("HF_TEST_DB")
    if p: return p
    d = tempfile.mkdtemp(); path = os.path.join(d, "t.db")
    bp = blueprint.load("blueprints/s1_provider_demo.json", members=400)
    res, _, _ = data_engine.run(bp, path, None, log=lambda s: None); assert res["status"] == "COMPLETED"
    return path

@pytest.fixture(scope="session")
# Read-only view of the dataset, used to find scenario rows (by scenario_id) and their NPI/TIN.
def con(db_path):
    c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row; return c

@pytest.fixture(scope="session")
# One app per session with fault injection off, so tests are deterministic. Tests that create things use their own app/state DB where isolation matters.
def client(db_path):
    app = create_app(Config(db_path=db_path, state_path=tempfile.mktemp(), fault_enabled=False))
    return TestClient(app)

@pytest.fixture(scope="session")
# Bearer header from the demo client credentials.
def hdr(client):
    r = client.post("/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": "hf-demo-client", "client_secret": "demo-secret-change-me"})
    return {"Authorization": "Bearer " + r.json()["access_token"]}

@pytest.fixture(scope="session")
# Look up the real TIN for an NPI; tests pass a wrong TIN explicitly to check that verification fails closed.
def tin(con):
    return lambda npi: con.execute("SELECT tin FROM provider WHERE npi=?", (npi,)).fetchone()[0]
