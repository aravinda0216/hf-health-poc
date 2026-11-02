# Sample data

Fixed low-index rows are always the scenario rows (PCS, PAS, PEV, PAP, PPI, PON, MBN/MCI/MPS/MPC/MAS, caregiver and expired-consent cases), so the same ids appear on any machine that builds the same blueprint. `scenario_id` says which scenario a row serves; `BASE` rows are the random bulk. Everything is synthetic: NPIs start with 9, `is_synthetic=1`, and the privacy scan checks for real-looking SSNs, phones and emails.

    sqlite3 out/dev/factory.db "select scenario_id,count(*) from claim_header group by 1"
