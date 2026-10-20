import os, tempfile, subprocess, sys
from hf_factory import blueprint, data_engine

# Scenario seeding: the fixed PCS, PAS and PEV scenarios must exist at any scale (the full rule set is enforced by the data engine's quality gate).
def test_all_rules_pass_and_scenarios_present(db_path, con):
    import json
    assert con.execute("SELECT COUNT(*) FROM claim_header WHERE scenario_id IN ('PCS-01','PCS-02','PCS-03','PCS-04')").fetchone()[0] >= 4
    assert {r[0] for r in con.execute("SELECT DISTINCT scenario_id FROM auth_request")} >= {"PAS-01", "PAS-02", "PAS-03", "PAS-04"}
    assert {r[0] for r in con.execute("SELECT DISTINCT scenario_id FROM member_demographics")} >= {"PEV-01", "PEV-02", "PEV-03"}

# Replay, re-seed and evolve checks (see repro.py) must pass.
def test_repro_script_passes():
    p = subprocess.run([sys.executable, "-m", "hf_factory.repro", "--members", "300"], capture_output=True, text=True); assert p.returncode == 0, p.stdout + p.stderr
