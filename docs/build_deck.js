// Rebuilds docs/Healthcare_AI_Factory_Phase1.pptx.  Run: NODE_PATH=$(npm root -g) node docs/build_deck.js
const pptxgen = require("pptxgenjs");
const pres = new pptxgen(); pres.layout = "LAYOUT_16x9"; pres.title = "Healthcare AI Platform Factory: Phase 1";
const DK = "0B3C49", TEAL = "0E7C86", ORG = "E07B39", CARD = "E6F1F1", INK = "1F2D33", MUTE = "5B6B72", W = "FFFFFF", HF = "Cambria", BF = "Calibri";
const shadow = () => ({ type: "outer", color: "000000", blur: 6, offset: 1.5, angle: 90, opacity: 0.12 });

function base(title, sub, notes) {
  const s = pres.addSlide(); s.background = { color: W };
  s.addText(title, { x: 0.5, y: 0.3, w: 9, h: 0.55, fontFace: HF, fontSize: 28, bold: true, color: DK, margin: 0, isTextBox: true });
  s.addText(sub, { x: 0.5, y: 0.86, w: 9, h: 0.3, fontFace: BF, fontSize: 13, color: MUTE, margin: 0, isTextBox: true });
  if (notes) s.addNotes(notes); return s;
}
const card = (s, x, y, w, h, fill = CARD) => s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: fill, width: 0 } });
const badge = (s, x, y, n, fill = TEAL) => { s.addShape(pres.shapes.OVAL, { x, y, w: 0.34, h: 0.34, fill: { color: fill }, line: { color: fill, width: 0 } });
  s.addText(String(n), { x, y, w: 0.34, h: 0.34, align: "center", valign: "middle", fontFace: BF, fontSize: 12, bold: true, color: W, margin: 0, isTextBox: true }); };
const bullets = (items, o = {}) => items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1, paraSpaceAfter: 3, ...o } }));

// 1 ---------------------------------------------------------------- scope
{ const s = base("Scope and assumptions", "What Phase 1 covers, what is stubbed, and what we assumed along the way",
    "Scope is the data engine and the API engine. The interpreter, blueprint engine, orchestrator and infra engine are separate work; four sample blueprint JSONs (S1 to S4) stand in for them.");
  card(s, 0.5, 1.4, 4.4, 2.35); badge(s, 0.65, 1.52, 1); s.addText("In scope", { x: 1.1, y: 1.52, w: 3.6, h: 0.34, fontFace: BF, fontSize: 16, bold: true, color: DK, margin: 0, valign: "middle", isTextBox: true });
  s.addText(bullets(["Data engine and API engine", "New entities and APIs from the blueprint", "11 built-in use cases, five scenarios", "Medicare Advantage, Medicaid, Commercial", "Control plane for stage jobs"], { fontFace: BF, fontSize: 13, color: INK }), { x: 0.7, y: 1.95, w: 4.1, h: 1.7, valign: "top", margin: 0, isTextBox: true });
  card(s, 0.5, 3.9, 4.4, 1.3); badge(s, 0.65, 4.02, 2, DK); s.addText("Stubbed or not built", { x: 1.1, y: 4.02, w: 3.6, h: 0.34, fontFace: BF, fontSize: 16, bold: true, color: DK, margin: 0, valign: "middle", isTextBox: true });
  s.addText(bullets(["Interpreter, blueprint, orchestrator: sample JSON", "Built-in entities stay hand-written"], { fontFace: BF, fontSize: 13, color: INK }), { x: 0.7, y: 4.42, w: 4.1, h: 0.75, valign: "top", margin: 0, isTextBox: true });
  card(s, 5.1, 1.4, 4.4, 3.8); badge(s, 5.25, 1.52, 3, ORG); s.addText("Assumptions", { x: 5.7, y: 1.52, w: 3.6, h: 0.34, fontFace: BF, fontSize: 16, bold: true, color: DK, margin: 0, valign: "middle", isTextBox: true });
  s.addText(bullets(["SQLite for the data; GCP services via profiles", "Templated text, no LLM", "Built at 1,000 members, scaled to 150,000", "Fixed seed and as-of date per scenario", "All data synthetic: reserved phone, email, NPI, TIN formats", "Appeal deadline 90 days: confirm with Compliance", "GCP code is unit-tested with fakes, not run live", "Generated APIs: create, get, list, update"], { fontFace: BF, fontSize: 13, color: INK }), { x: 5.3, y: 1.95, w: 4.1, h: 3.15, valign: "top", margin: 0, isTextBox: true });
}

// 2 ---------------------------------------------------------------- results
{ const s = base("Results", "Every check passes at 1,000 and at 50,000 members; a full 50K build takes about 30 seconds",
    "From the last runs: make demo, make demo-full (827,408 rows, 31 of 31 rules, privacy scan PASS, 48/48 contract tests for S1), the 22-operation contract run (88/88), 44 of 44 smoke scenarios, and the full suite of 148 tests.");
  const st = [["11", "use cases", "6 provider, 5 member"], ["44", "smoke scenarios", "Four stories per use case"], ["22", "API operations", "Each also an agent tool"],
              ["37", "data rules", "31 built in, 6 generated"], ["827K", "rows at 50K members", "35 tables, about 30 s"], ["148", "automated tests", "Contract, scenario, dynamic"]];
  st.forEach((t, i) => { const x = 0.5 + (i % 3) * 3.05, y = 1.4 + Math.floor(i / 3) * 1.55;
    card(s, x, y, 2.9, 1.4);
    s.addText(t[0], { x: x + 0.15, y: y + 0.15, w: 1.5, h: 0.7, fontFace: HF, fontSize: 36, bold: true, color: TEAL, margin: 0, valign: "middle", isTextBox: true });
    s.addText(t[1], { x: x + 1.55, y: y + 0.15, w: 1.3, h: 0.7, fontFace: BF, fontSize: 13, color: DK, bold: true, margin: 0, valign: "middle", isTextBox: true });
    s.addText(t[2], { x: x + 0.15, y: y + 0.9, w: 2.65, h: 0.4, fontFace: BF, fontSize: 12, color: MUTE, margin: 0, isTextBox: true }); });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 4.65, w: 9, h: 0.6, fill: { color: DK }, line: { color: DK, width: 0 } });
  s.addText([{ text: "Next: ", options: { bold: true } }, { text: "run the GCP mode on a real project, convert built-in entities to definitions, richer declarative rules" }], { x: 0.7, y: 4.65, w: 8.6, h: 0.6, fontFace: BF, fontSize: 13, color: W, valign: "middle", margin: 0, isTextBox: true });
}

// 3 ---------------------------------------------------------------- data engine
{ const s = base("Data engine", "Every row is a pure function of the seed, so any run can be replayed exactly",
    "Stage s6 runs after s5 because transition-of-care rows and rule R-10 need claims; the spec lists s6 as parallel with s4. Distributions (line of business, language, geography, caregiver rate, denial rate) come from the blueprint.");
  const st = [["s3", "Reference", "Plans by line of business, providers, locations, slots"], ["s4", "Members", "Age, language, geography, caregivers, PCPs"], ["s5", "Claims and auth", "Denial and pend rates from the blueprint"], ["s6", "Knowledge + custom", "FAQs, SOPs, golden Q&A, blueprint entities"], ["s7", "Validate", "31 + generated rules, privacy scan, manifest"]];
  st.forEach((t, i) => { const x = 0.5 + i * 1.82; card(s, x, 1.4, 1.7, 2.0, i === 4 ? "FBE7D9" : CARD);
    s.addShape(pres.shapes.OVAL, { x: x + 0.12, y: 1.52, w: 0.4, h: 0.4, fill: { color: i === 4 ? ORG : TEAL }, line: { color: W, width: 0 } });
    s.addText(t[0], { x: x + 0.12, y: 1.52, w: 0.4, h: 0.4, align: "center", valign: "middle", fontFace: BF, fontSize: 11, bold: true, color: W, margin: 0, isTextBox: true });
    s.addText(t[1], { x: x + 0.12, y: 2.0, w: 1.5, h: 0.35, fontFace: BF, fontSize: 14, bold: true, color: DK, margin: 0, isTextBox: true });
    s.addText(t[2], { x: x + 0.12, y: 2.38, w: 1.5, h: 0.95, fontFace: BF, fontSize: 12, color: INK, margin: 0, valign: "top", isTextBox: true }); });
  const cs = [["Deterministic", "Seed, stage, entity and row number drive every value. No clock, no worker effects"], ["Validated", "31 built-in rules plus rules generated per custom entity, per-table checksums"], ["Repeatable", "Replay matches, a new seed differs, 20% more members keeps earlier rows"]];
  cs.forEach((t, i) => { const x = 0.5 + i * 3.05; card(s, x, 3.6, 2.9, 1.6); s.addText(t[0], { x: x + 0.15, y: 3.7, w: 2.6, h: 0.35, fontFace: BF, fontSize: 15, bold: true, color: TEAL, margin: 0, isTextBox: true });
    s.addText(t[1], { x: x + 0.15, y: 4.08, w: 2.6, h: 1.05, fontFace: BF, fontSize: 13, color: INK, margin: 0, valign: "top", isTextBox: true }); });
}

// 4 ---------------------------------------------------------------- API engine
{ const s = base("API engine", "A FastAPI service over the synthetic data, built for voice and chat agent testing",
    "OpenAPI 3.1, base path /v1. Provider calls verify NPI and TIN; member calls verify date of birth and last name, and caregivers add the consent record. Any failure returns the same 403 and discloses nothing. The use_cases in the blueprint decide which routes are live.");
  const grp = (y, label, items) => { s.addText(label, { x: 0.5, y, w: 5.6, h: 0.3, fontFace: BF, fontSize: 13, bold: true, color: DK, margin: 0, isTextBox: true });
    items.forEach((t, i) => { const x = 0.5 + (i % 3) * 1.9, yy = y + 0.34 + Math.floor(i / 3) * 0.6; card(s, x, yy, 1.8, 0.52);
      s.addText([{ text: t[0] + "  ", options: { bold: true, color: TEAL } }, { text: t[1], options: { color: INK } }], { x: x + 0.08, y: yy, w: 1.7, h: 0.52, fontFace: BF, fontSize: 12, valign: "middle", margin: 0, isTextBox: true }); }); };
  grp(1.35, "Provider (S1, S3, S4)", [["PCS", "Claim status"], ["PAS", "Auth status"], ["PEV", "Eligibility"], ["PAP", "Appeals"], ["PPI", "Payments"], ["PON", "Onboarding"]]);
  grp(3.0, "Member (S2, S4)", [["MBN", "Benefits"], ["MCI", "Claims"], ["MPS", "Find a provider"], ["MPC", "Change PCP"], ["MAS", "Appointments"], ["Both", "Identity, knowledge"]]);
  s.addText("22 built-in operations plus any blueprint endpoints, each an agent tool", { x: 0.5, y: 4.85, w: 5.6, h: 0.3, fontFace: BF, fontSize: 12, italic: true, color: MUTE, margin: 0, isTextBox: true });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.3, y: 1.35, w: 3.2, h: 3.85, fill: { color: DK }, line: { color: DK, width: 0 }, shadow: shadow() });
  s.addText("Built-in guardrails", { x: 6.5, y: 1.45, w: 2.8, h: 0.4, fontFace: BF, fontSize: 16, bold: true, color: W, margin: 0, isTextBox: true });
  s.addText(bullets(["OAuth2 token on every call", "Failed verification discloses nothing", "Caregiver consent and scope checked", "Writes support dry run", "Writes never touch the dataset", "Injected faults and latency", "Tool manifest with escalate and never rules"], { fontFace: BF, fontSize: 12.5, color: W }), { x: 6.5, y: 1.9, w: 2.85, h: 3.2, valign: "top", margin: 0, isTextBox: true });
}

// 5 ---------------------------------------------------------------- scenarios
{ const s = base("Five scenarios", "Each blueprint sets the line of business, size and mix; the same engines build all five",
    "Blueprints live in blueprints/. S2 and S3 were built and verified at reduced size (1,000 and 5,000 members); the full 100,000 and 150,000 member sizes were not run. S5 was built at its full 15,000 members: 276,147 rows, 37 of 37 rules, 40 of 40 contract tests, 6 of 6 generated smoke scenarios, about 9 seconds.");
  const sc = [["S1", "Provider, Medicare Advantage", ["50,000 members", "6 provider use cases", "Fault injection on"], TEAL],
              ["S2", "Member, Medicaid", ["100,000 members", "35% Spanish, 40% rural", "25% caregivers, 15% edge cases"], ORG],
              ["S3", "Scale test, Commercial", ["150,000 members", "About 1M claims, 30% denials", "30% edge cases"], TEAL],
              ["S4", "Sandbox, Commercial", ["20,000 members", "Benefits, claims, claim status", "Higher rate limit"], ORG]];
  sc.forEach((t, i) => { const x = 0.5 + (i % 2) * 4.6, y = 1.3 + Math.floor(i / 2) * 1.55; card(s, x, y, 4.4, 1.45);
    s.addShape(pres.shapes.RECTANGLE, { x: x + 0.15, y: y + 0.12, w: 0.7, h: 0.45, fill: { color: t[3] }, line: { color: t[3], width: 0 } });
    s.addText(t[0], { x: x + 0.15, y: y + 0.12, w: 0.7, h: 0.45, align: "center", valign: "middle", fontFace: HF, fontSize: 18, bold: true, color: W, margin: 0, isTextBox: true });
    s.addText(t[1], { x: x + 1.0, y: y + 0.12, w: 3.3, h: 0.45, fontFace: BF, fontSize: 15, bold: true, color: DK, valign: "middle", margin: 0, isTextBox: true });
    s.addText(bullets(t[2], { fontFace: BF, fontSize: 12.5, color: INK }), { x: x + 0.2, y: y + 0.65, w: 4.0, h: 0.75, valign: "top", margin: 0, isTextBox: true }); });
  card(s, 0.5, 4.4, 9.0, 0.85, "FBE7D9");
  s.addShape(pres.shapes.RECTANGLE, { x: 0.65, y: 4.55, w: 0.7, h: 0.45, fill: { color: DK }, line: { color: DK, width: 0 } });
  s.addText("S5", { x: 0.65, y: 4.55, w: 0.7, h: 0.45, align: "center", valign: "middle", fontFace: HF, fontSize: 18, bold: true, color: W, margin: 0, isTextBox: true });
  s.addText([{ text: "Defined in the blueprint: ", options: { bold: true, color: DK } }, { text: "15,000 members with claims and eligibility APIs, plus Appointment and Referral entities and a booking API, with no code", options: { color: INK } }], { x: 1.5, y: 4.4, w: 7.9, h: 0.85, fontFace: BF, fontSize: 13, valign: "middle", margin: 0, isTextBox: true });
}

// 5b --------------------------------------------------------------- blueprint-driven
{ const s = base("Blueprint-defined entities and APIs", "Add an entity and an endpoint to the blueprint; the factory builds the table, the data and the API",
    "This answers the review: new data and API capabilities come from the blueprint definition. The entity definition on the left is the real Appointment entity from blueprints/s5_custom_appointments.json (shortened). Nothing in the code mentions appointments for this path. The same mechanism built an unrelated refill-request entity in the tests.");
  s.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 1.35, w: 4.7, h: 3.85, fill: { color: DK }, line: { color: DK, width: 0 }, shadow: shadow() });
  const code = ['"Appointment": {', '  "count": {"ratio": 0.8 of members},', '  "fields": [', '    appointment_id   id (key)', '    member_id        ref member', '    provider_id      ref provider', '    appointment_date date', '    start_time       enum', '    status           enum, BOOKED', '  ],', '  "unique": provider, date, time', '}', '', 'POST /appointments  (create)', 'PATCH /appointments/{id}  (update)'];
  s.addText(code.map((t, i) => ({ text: t, options: { breakLine: i < code.length - 1 } })), { x: 0.7, y: 1.45, w: 4.4, h: 3.65, fontFace: "Courier New", fontSize: 11.5, color: W, valign: "top", margin: 0, isTextBox: true });
  s.addText("The factory generates", { x: 5.45, y: 1.35, w: 4.05, h: 0.35, fontFace: BF, fontSize: 15, bold: true, color: DK, margin: 0, isTextBox: true });
  const gen = [["Data", "Table, indexes, 12,000 seeded records"], ["Rules", "Keys, enums, ranges, unique, references"], ["API", "Typed routes, dry run, 409 on conflict"], ["Contract", "OpenAPI and agent tool, 4 tests per endpoint"], ["Smoke", "Create, read back, conflict, cancel, list"]];
  gen.forEach((t, i) => { const y = 1.8 + i * 0.68; card(s, 5.45, y, 4.05, 0.6);
    s.addShape(pres.shapes.OVAL, { x: 5.55, y: y + 0.12, w: 0.36, h: 0.36, fill: { color: i % 2 ? ORG : TEAL }, line: { color: W, width: 0 } });
    s.addText(String(i + 1), { x: 5.55, y: y + 0.12, w: 0.36, h: 0.36, align: "center", valign: "middle", fontFace: BF, fontSize: 11, bold: true, color: W, margin: 0, isTextBox: true });
    s.addText([{ text: t[0] + "  ", options: { bold: true, color: DK } }, { text: t[1], options: { color: INK } }], { x: 6.05, y: y, w: 3.4, h: 0.6, fontFace: BF, fontSize: 12, valign: "middle", margin: 0, isTextBox: true }); });
}

// 5c --------------------------------------------------------------- what it can and cannot do
{ const s = base("What the blueprint can express", "S5 ran at full size: 15,000 members, 276K rows, all gates passed in about 9 seconds",
    "Capabilities are declarative. The limits matter: the built-in entities and their business rules are still hand-written, and anything beyond create, get, list and update with declared rules (for example an availability window or a 24-hour cancellation rule) needs code or a new declarative rule type. There is no LLM in this path.");
  const st = [["37/37", "data rules"], ["40/40", "contract tests"], ["6/6", "smoke scenarios"]];
  st.forEach((t, i) => { const x = 0.5 + i * 3.05; card(s, x, 1.3, 2.9, 0.9);
    s.addText(t[0], { x: x + 0.15, y: 1.3, w: 1.5, h: 0.9, fontFace: HF, fontSize: 26, bold: true, color: TEAL, valign: "middle", margin: 0, isTextBox: true });
    s.addText(t[1], { x: x + 1.6, y: 1.3, w: 1.25, h: 0.9, fontFace: BF, fontSize: 12.5, bold: true, color: DK, valign: "middle", margin: 0, isTextBox: true }); });
  card(s, 0.5, 2.4, 4.4, 2.85); s.addText("Can express", { x: 0.7, y: 2.5, w: 4.0, h: 0.35, fontFace: BF, fontSize: 15, bold: true, color: TEAL, margin: 0, isTextBox: true });
  s.addText(bullets(["Field types, ranges, enums with weights", "References to built-in or custom tables", "Counts: fixed, ratio, per parent", "Create, get, list, update endpoints", "Unique rules, status transitions", "Member verification on any endpoint"], { fontFace: BF, fontSize: 12.5, color: INK }), { x: 0.7, y: 2.9, w: 4.0, h: 2.3, valign: "top", margin: 0, isTextBox: true });
  card(s, 5.1, 2.4, 4.4, 2.85, "FBE7D9"); s.addText("Cannot yet", { x: 5.3, y: 2.5, w: 4.0, h: 0.35, fontFace: BF, fontSize: 15, bold: true, color: ORG, margin: 0, isTextBox: true });
  s.addText(bullets(["Built-in entities are not definitions yet", "Logic beyond declared rules, such as availability windows", "Free text to blueprint: that is the interpreter", "Durable state on Cloud Run", "No LLM in this path"], { fontFace: BF, fontSize: 12.5, color: INK }), { x: 5.3, y: 2.9, w: 4.0, h: 2.3, valign: "top", margin: 0, isTextBox: true });
}

// 6 ---------------------------------------------------------------- run modes
{ const s = base("Run modes and GCP", "One API, three places for the data, three ways to search knowledge",
    "HF_PROFILE picks where data lives (local, memory, gcp); HF_SEARCH picks the search (lexical, vector, vertex). The Vertex AI embedding and Vector Search code, the GCS dataset download and the publish tool follow the installed Google client signatures and are tested with fakes only. Nothing has run against a real GCP project. Durable state (Firestore or Cloud SQL) and AlloyDB are not built.");
  const cols = [["Local", "HF_PROFILE=local", ["Dataset: SQLite file", "State: SQLite file", "Search: BM25 lexical"], TEAL],
                ["In memory", "HF_PROFILE=memory", ["Dataset copied into RAM", "State gone on exit; no disk writes", "Search: BM25 or in-memory vectors"], TEAL],
                ["GCP", "HF_PROFILE=gcp", ["Dataset from Cloud Storage", "State on the instance (ephemeral)", "Search: Vertex AI embeddings + Vector Search"], ORG]];
  cols.forEach((t, i) => { const x = 0.5 + i * 3.05; card(s, x, 1.4, 2.9, 2.75);
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.4, w: 2.9, h: 0.75, fill: { color: t[3] }, line: { color: t[3], width: 0 } });
    s.addText(t[0], { x: x + 0.15, y: 1.42, w: 2.6, h: 0.4, fontFace: BF, fontSize: 17, bold: true, color: W, margin: 0, isTextBox: true });
    s.addText(t[1], { x: x + 0.15, y: 1.8, w: 2.6, h: 0.3, fontFace: "Courier New", fontSize: 11, color: W, margin: 0, isTextBox: true });
    s.addText(bullets(t[2], { fontFace: BF, fontSize: 13, color: INK }), { x: x + 0.15, y: 2.3, w: 2.65, h: 1.75, valign: "top", margin: 0, isTextBox: true }); });
  s.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 4.35, w: 9, h: 0.85, fill: { color: "FBE7D9" }, line: { color: "FBE7D9", width: 0 } });
  s.addText([{ text: "Honest status: ", options: { bold: true, color: DK } }, { text: "the Vertex AI and Cloud Storage code is unit-tested with fakes and has not run on a live project. Durable state and AlloyDB are not built.", options: { color: INK } }], { x: 0.7, y: 4.35, w: 8.6, h: 0.85, fontFace: BF, fontSize: 13, valign: "middle", margin: 0, isTextBox: true });
}

// 7 ---------------------------------------------------------------- run it
{ const s = base("Run it locally", "Requires Python 3.10 or later; run each command from the project folder",
    "make test and make demo both rebuild a dataset, so they take a few seconds. If tests fail with an unexpected keyword argument app, upgrade fastapi, starlette and httpx. The Docker image has not been built in the build environment (no Docker daemon); the entrypoint script was run directly.");
  const step = (x, y, w, t, sub, fill = TEAL) => { s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 0.85, fill: { color: fill }, line: { color: fill, width: 0 }, shadow: shadow() });
    s.addText([{ text: t, options: { bold: true, fontSize: 14, breakLine: true } }, { text: sub, options: { fontSize: 11.5 } }], { x: x + 0.1, y, w: w - 0.2, h: 0.85, fontFace: BF, color: W, valign: "middle", margin: 0, isTextBox: true }); };
  const arrow = (x1, y1, x2, y2) => s.addShape(pres.shapes.LINE, { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), line: { color: MUTE, width: 1.5, endArrowType: "triangle" }, flipH: x2 < x1 });
  step(0.5, 1.4, 2.0, "1  make install", "Installs requirements"); arrow(2.5, 1.825, 2.75, 1.825);
  step(2.75, 1.4, 2.0, "2  make test", "148 tests should pass"); arrow(4.75, 1.825, 5.0, 1.825);
  step(5.0, 1.4, 2.0, "3  make demo", "1,000 members, 2 s"); arrow(7.0, 1.825, 7.25, 1.825);
  step(7.25, 1.4, 2.25, "4  Pick a mode", "serve, serve-memory or serve-gcp", DK);
  step(0.5, 2.7, 2.9, "make serve", "API on port 8080, token required"); step(3.55, 2.7, 2.9, "make serve-memory", "No files written; vector search"); step(6.6, 2.7, 2.9, "docker compose up", "API (memory) and control plane", ORG);
  card(s, 0.5, 3.85, 9, 1.35);
  s.addText(bullets(["Optional: make demo-full builds 50,000 members; make repro checks replay, re-seed and evolve", "Try the custom-entity blueprint: python3 -m hf_factory.run --blueprint blueprints/s5_custom_appointments.json", "Postman collections for the provider and member APIs are in postman/; run them with newman", "GCP steps (publish, then serve-gcp) are in the README; Docker was not built here"], { fontFace: BF, fontSize: 12.5, color: INK }), { x: 0.7, y: 3.95, w: 8.6, h: 1.2, valign: "top", margin: 0, isTextBox: true });
}
pres.writeFile({ fileName: "docs/Healthcare_AI_Factory_Phase1.pptx" }).then(() => console.log("written"));
