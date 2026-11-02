"""S1-S4 blueprints: each builds end to end at small scale and its distributions show up in the data."""
import json, os, sqlite3, pytest
from hf_factory import blueprint, data_engine

BP = os.path.join(os.path.dirname(__file__), "..", "blueprints")


def build(name, tmp_path, members=1500):
    bp = blueprint.load(os.path.join(BP, name), members=members)
    res, rep, man = data_engine.run(bp, str(tmp_path / "f.db"), log=lambda *_: None)
    return res, sqlite3.connect(str(tmp_path / "f.db"))


@pytest.mark.parametrize("name", ["s1_provider_demo.json", "s2_medicaid_member.json", "s3_commercial_scale.json", "s4_commercial_sandbox.json"])
def test_all_rules_pass(name, tmp_path):
    res, _ = build(name, tmp_path)
    assert res["status"] == "COMPLETED" and res["validation"]["failed"] == 0


def test_s2_distributions(tmp_path):
    _, c = build("s2_medicaid_member.json", tmp_path, 3000)
    n = c.execute("select count(*) from member_demographics").fetchone()[0]
    es = c.execute("select count(*) from member_demographics where preferred_language='ES'").fetchone()[0] / n
    rural = c.execute("select count(*) from member_demographics where geo_type='RURAL'").fetchone()[0] / n
    assert 0.30 < es < 0.40 and 0.33 < rural < 0.47
    assert {r[0] for r in c.execute("select lob from plan")} == {"MEDICAID_MCO"}


def test_s3_denial_rate_and_claim_volume(tmp_path):
    _, c = build("s3_commercial_scale.json", tmp_path, 3000)
    n = c.execute("select count(*) from claim_header").fetchone()[0]
    den = c.execute("select count(*) from claim_header where status='DENIED'").fetchone()[0] / n
    assert 0.25 < den < 0.40 and n > 3000 * 5      # ~6.7 claims per member


def test_deterministic(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    r1, _ = build("s2_medicaid_member.json", tmp_path / "a", 500); r2, _ = build("s2_medicaid_member.json", tmp_path / "b", 500)
    assert r1["total_rows"] == r2["total_rows"]


def test_profiles(monkeypatch):
    from hf_factory import profiles
    assert profiles.dataset_store("x.db", "local").path == "x.db"
    with pytest.raises(ValueError): profiles.state_store("x", "nope")
    monkeypatch.delenv("HF_DATASET_URI", raising=False)
    with pytest.raises(ValueError): profiles.dataset_store("missing.db", "gcp")          # gcp needs a gs:// URI
    from hf_factory.adapters.gcp_store import NotImplementedOnGcp, AlloyDbDataset
    with pytest.raises(NotImplementedOnGcp): AlloyDbDataset("dsn").connect_ro()
