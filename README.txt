================================================================================
  GUARDIAN P  —  Solar PV Anomaly Detection System
  README  (plain text — open with TextEdit or any text editor)
================================================================================

Guardian P monitors solar inverter output in real time. It combines a
physics-constraint engine with an optional Claude AI reasoning layer to detect,
classify, and explain anomalies before bad data reaches downstream models.


────────────────────────────────────────────────────────────────────────────────
  TABLE OF CONTENTS
────────────────────────────────────────────────────────────────────────────────

  1. Project Structure
  2. Requirements
  3. Installation
  4. Configuration
  5. Running the API Server
  6. Loading Archive Data (batch mode)
  7. Running Tests
  8. API Reference & Examples
     8.1  POST  /analyse          — analyse a single data point
     8.2  POST  /analyse/batch    — analyse multiple data points
     8.3  POST  /feedback         — submit operator feedback
     8.4  GET   /learning/state   — view self-learning status
     8.5  GET   /inverters/{id}/stats — per-inverter anomaly stats
     8.6  GET   /alerts           — query stored alerts by filter
     8.7  POST  /diagnose         — read-only real-time diagnosis
  9. Anomaly Types & Severity
  10. Common Problems & Solutions


────────────────────────────────────────────────────────────────────────────────
  1. PROJECT STRUCTURE
────────────────────────────────────────────────────────────────────────────────

  guardian_p/
  ├── api/
  │   └── main.py               FastAPI application (all HTTP endpoints)
  ├── core/
  │   ├── physics_engine.py     Physics constraint rules (PV-001 … PV-005)
  │   ├── reasoning_engine.py   Claude AI reasoning layer + rule-based fallback
  │   └── feedback_loop.py      Self-learning feedback persistence
  ├── data/
  │   └── load_archive.py       CSV ingestion with auto-detection heuristics
  ├── tests/
  │   ├── test_physics_engine.py   13 unit tests
  │   ├── test_reasoning_engine.py 23 unit tests
  │   └── test_load_archive.py     41 unit tests
  ├── requirements.txt
  └── README.txt                (this file)


────────────────────────────────────────────────────────────────────────────────
  2. REQUIREMENTS
────────────────────────────────────────────────────────────────────────────────

  • Python 3.11 or later
  • pip (comes with Python)
  • An Anthropic API key (optional — system works without it using rule-based
    fallback reasoning)

  Python packages (pinned versions):

    fastapi==0.111.0
    uvicorn[standard]==0.29.0
    pydantic==2.7.1
    anthropic==0.28.0


────────────────────────────────────────────────────────────────────────────────
  3. INSTALLATION
────────────────────────────────────────────────────────────────────────────────

  Step 1 — Open Terminal (Applications → Utilities → Terminal)

  Step 2 — Go to the project directory:

    cd ~/Desktop/guardian_p

  Step 3 — (Recommended) Create an isolated virtual environment:

    python3 -m venv .venv
    source .venv/bin/activate

    You will see (.venv) at the start of your terminal prompt.
    Run "source .venv/bin/activate" again any time you open a new Terminal tab.

  Step 4 — Install dependencies:

    pip install -r requirements.txt

  That's it. No build step, no database setup.


────────────────────────────────────────────────────────────────────────────────
  4. CONFIGURATION
────────────────────────────────────────────────────────────────────────────────

  AI reasoning (optional)
  -----------------------
  Set your Anthropic API key so Guardian P can generate natural-language
  explanations via Claude:

    export ANTHROPIC_API_KEY="sk-ant-..."

  Add that line to ~/.zshrc (or ~/.bash_profile) to make it permanent.

  Without the key the system still works — every violation gets a detailed
  rule-based explanation instead of an AI-generated one. No functionality
  is lost; only the phrasing of the explanation changes.

  Archive data location
  ---------------------
  Batch mode expects four CSV files at:

    ~/Desktop/archive/Plant_1_Generation_Data.csv
    ~/Desktop/archive/Plant_1_Weather_Sensor_Data.csv
    ~/Desktop/archive/Plant_2_Generation_Data.csv
    ~/Desktop/archive/Plant_2_Weather_Sensor_Data.csv

  The path is set in data/load_archive.py (ARCHIVE variable, line 36).
  Edit it if your files are elsewhere.


────────────────────────────────────────────────────────────────────────────────
  5. RUNNING THE API SERVER
────────────────────────────────────────────────────────────────────────────────

  From the guardian_p directory:

    uvicorn api.main:app --reload --port 8000

  The server starts at:  http://127.0.0.1:8000

  Interactive API docs (auto-generated):
    http://127.0.0.1:8000/docs        (Swagger UI — try endpoints in browser)
    http://127.0.0.1:8000/redoc       (ReDoc — cleaner reference view)

  Press Ctrl+C to stop the server.


────────────────────────────────────────────────────────────────────────────────
  6. LOADING ARCHIVE DATA (BATCH MODE)
────────────────────────────────────────────────────────────────────────────────

  Processes all 136,476 rows from both plants, auto-detects units, runs
  physics rules, and prints a summary:

    cd ~/Desktop/guardian_p
    python3 data/load_archive.py

  Expected output (takes ~3 seconds):

    08:30:01  INFO  Processing PLANT_1
    08:30:01  INFO  Irradiance unit: raw max=1.2217  →  ×1000  (peak ≈ 1221.7 W/m²)
    08:30:01  INFO  DC_POWER unit: median DC/AC=10.22  →  ÷10  (stored as ×0.1 kW)
    08:30:01  INFO  Capacity/inv: 22 inverters  p95 peak=1410.5 kW  →  rated ≈ 1551.6 kW
    ...
    Total data points analysed : 136,476
    Flagged data points        : 4,847  (3.6%)
    Top anomaly types:
      MPPT_FAILURE   : 3,847
      STRING_SHADING :   765
      OVER_POWER     :   650


────────────────────────────────────────────────────────────────────────────────
  7. RUNNING TESTS
────────────────────────────────────────────────────────────────────────────────

  All tests work with the standard library — no pytest required (though pytest
  works too).

  Run all three suites:

    python3 tests/test_physics_engine.py
    python3 tests/test_reasoning_engine.py
    python3 tests/test_load_archive.py

  Or with pytest (shows individual test names):

    python3 -m pytest tests/ -v

  Expected: 77 tests, 77 passed, 0 failed.

  NOTE: test_reasoning_engine.py reads from ~/Desktop/archive. Make sure the
  archive CSV files are present before running that suite.


────────────────────────────────────────────────────────────────────────────────
  8. API REFERENCE & EXAMPLES
────────────────────────────────────────────────────────────────────────────────

  All examples use curl. You can also use the browser at
  http://127.0.0.1:8000/docs to try them interactively.

  Every response includes two headers:
    X-Request-ID   — unique ID for tracing this request in logs
    X-Duration-Ms  — server processing time in milliseconds


  ── 8.1  POST /analyse  ─────────────────────────────────────────────────────

  Analyse a single inverter data point.

  Request:

    curl -s -X POST http://127.0.0.1:8000/analyse \
      -H "Content-Type: application/json" \
      -d '{
        "timestamp":     "2024-01-15T10:30:00",
        "inverter_id":   "plant_1::INV-07",
        "power_kw":      850.0,
        "irradiance_wm2": 780.0,
        "voltage_v":     320.0,
        "current_a":     2.65,
        "temperature_c": 42.0,
        "capacity_kw":   1500.0
      }'

  Response (clean — no violations):

    {
      "is_clean": true,
      "violations": [],
      "reasoning": [],
      "alert_id": null,
      "is_blocked": false
    }

  Response (anomaly detected):

    {
      "is_clean": false,
      "violations": [
        {
          "type": "MPPT_FAILURE",
          "severity": "warning",
          "rule_id": "PV-001",
          "message": "Power output 120.0 kW is well below irradiance-expected ...",
          "measured": 120.0,
          "expected_min": 520.0,
          "expected_max": 780.0,
          "confidence": 0.88
        }
      ],
      "reasoning": [
        {
          "anomaly_type": "MPPT_FAILURE",
          "severity": "warning",
          "confidence_score": 0.87,
          "root_cause": "Inverter is not tracking maximum power point ...",
          "supporting_facts": ["Measured value: 120.0", ...],
          "recommended_actions": [
            "Check inverter MPPT algorithm settings and firmware version",
            "Inspect DC string combiner box for loose connections",
            ...
          ],
          "urgency": "Respond within 24 hours",
          "clean_data_action": "DATA FLAGGED — forwarded with anomaly tag",
          "reasoning_source": "rule_based"
        }
      ],
      "alert_id": "a3f2c1d8",
      "is_blocked": false
    }

  Field reference:
    timestamp       ISO-8601 datetime string (required)
    inverter_id     String, no "/" character (required)
    power_kw        0.0 – 50000.0  kW
    irradiance_wm2  0.0 – 1500.0   W/m²
    voltage_v       0.0 – 1500.0   V
    current_a       0.0 – 1000.0   A
    temperature_c   -40.0 – 100.0  °C
    capacity_kw     0.1 – 50000.0  kW  (inverter nameplate rating)


  ── 8.2  POST /analyse/batch  ───────────────────────────────────────────────

  Analyse up to 1,000 data points in one request.

  Request:

    curl -s -X POST http://127.0.0.1:8000/analyse/batch \
      -H "Content-Type: application/json" \
      -d '{
        "data_points": [
          {
            "timestamp": "2024-01-15T10:30:00",
            "inverter_id": "plant_1::INV-07",
            "power_kw": 850.0,
            "irradiance_wm2": 780.0,
            "voltage_v": 320.0,
            "current_a": 2.65,
            "temperature_c": 42.0,
            "capacity_kw": 1500.0
          },
          {
            "timestamp": "2024-01-15T10:30:00",
            "inverter_id": "plant_1::INV-08",
            "power_kw": 0.0,
            "irradiance_wm2": 780.0,
            "voltage_v": 320.0,
            "current_a": 0.0,
            "temperature_c": 41.0,
            "capacity_kw": 1500.0
          }
        ]
      }'

  Response:

    {
      "total": 2,
      "clean": 1,
      "flagged": 1,
      "results": [ ... ]
    }


  ── 8.3  POST /feedback  ────────────────────────────────────────────────────

  Submit operator feedback on an alert. Guardian P uses this to adjust
  confidence scores automatically (self-learning).

  Request:

    curl -s -X POST http://127.0.0.1:8000/feedback \
      -H "Content-Type: application/json" \
      -d '{
        "alert_id":         "a3f2c1d8",
        "inverter_id":      "plant_1::INV-07",
        "anomaly_type":     "MPPT_FAILURE",
        "is_false_positive": false,
        "operator_note":    "Confirmed: MPPT fault repaired at 14:00"
      }'

  Response:

    {
      "status": "accepted",
      "alert_id": "a3f2c1d8",
      "anomaly_type": "MPPT_FAILURE",
      "feedback": "confirmed",
      "updated_confidence": 0.871,
      "total_feedback_count": 1
    }

  Valid anomaly_type values:
    OVER_POWER, MPPT_FAILURE, STRING_SHADING, OVERVOLTAGE, UNDERVOLTAGE,
    THERMAL_LIMIT, SENSOR_FAULT, SENSOR_DRIFT, DROPOUT, ENERGY_BALANCE

  Feedback is persisted to data/feedback.jsonl and replayed on restart.


  ── 8.4  GET /learning/state  ───────────────────────────────────────────────

  View current self-learned confidence adjustments for all anomaly types.

  Request:

    curl -s http://127.0.0.1:8000/learning/state

  Response:

    {
      "MPPT_FAILURE": {
        "baseline": 0.87,
        "adjustment": 0.01,
        "effective": 0.88
      },
      "OVER_POWER": {
        "baseline": 0.94,
        "adjustment": -0.04,
        "effective": 0.90
      },
      ...
    }

  Each false-positive report reduces confidence by 0.04.
  Each confirmed detection increases it by 0.01.
  Adjustments are clamped to [-0.20, +0.10].


  ── 8.5  GET /inverters/{inverter_id}/stats  ────────────────────────────────

  Historical anomaly statistics for one inverter (uses in-memory alert store;
  populated as /analyse calls are made during the current server session).

  Request:

    curl -s "http://127.0.0.1:8000/inverters/plant_1::INV-07/stats"

  Note: inverter_id contains "::" — URL-encode it as %3A%3A if needed:

    curl -s "http://127.0.0.1:8000/inverters/plant_1%3A%3AINV-07/stats"

  Response:

    {
      "inverter_id": "plant_1::INV-07",
      "total_alerts": 12,
      "block_rate_pct": 8.3,
      "by_anomaly_type": {
        "MPPT_FAILURE": 9,
        "STRING_SHADING": 3
      },
      "by_severity": {
        "warning": 12
      },
      "recent_alerts": [ ... last 5 alerts ... ]
    }

  Returns HTTP 404 if no alerts have been recorded for that inverter yet.


  ── 8.6  GET /alerts  ───────────────────────────────────────────────────────

  Query the in-memory alert store with up to five filters (all optional).

  Parameters:
    inverter_id    Filter by exact inverter ID
    anomaly_type   One of the 10 valid type strings (see 8.3)
    severity       critical | warning | info
    since          ISO-8601 datetime — return alerts at or after this time
    until          ISO-8601 datetime — return alerts before or at this time

  Examples:

    # All alerts for one inverter:
    curl -s "http://127.0.0.1:8000/alerts?inverter_id=plant_1::INV-07"

    # All CRITICAL alerts since a given time:
    curl -s "http://127.0.0.1:8000/alerts?severity=critical&since=2024-01-15T09:00:00"

    # MPPT failures for one inverter:
    curl -s "http://127.0.0.1:8000/alerts?inverter_id=plant_1::INV-07&anomaly_type=MPPT_FAILURE"

  Response:

    {
      "count": 3,
      "alerts": [
        {
          "alert_id": "a3f2c1d8",
          "timestamp": "2024-01-15T10:30:00",
          "inverter_id": "plant_1::INV-07",
          "is_blocked": false,
          "violations": [ ... ]
        },
        ...
      ]
    }


  ── 8.7  POST /diagnose  ────────────────────────────────────────────────────

  Read-only diagnosis — identical physics analysis as /analyse but does NOT
  update the alert store or the last-seen inverter state. Use for ad-hoc
  checks without side effects.

  Request:

    curl -s -X POST http://127.0.0.1:8000/diagnose \
      -H "Content-Type: application/json" \
      -d '{
        "timestamp":     "2024-01-15T10:30:00",
        "inverter_id":   "plant_1::INV-07",
        "power_kw":      50.0,
        "irradiance_wm2": 800.0,
        "voltage_v":     320.0,
        "current_a":     0.15,
        "temperature_c": 42.0,
        "capacity_kw":   1500.0
      }'

  Response:

    {
      "verdict": "WARNING",
      "summary": "1 violation(s) detected: MPPT_FAILURE",
      "data_quality": "FLAGGED",
      "violations": [
        {
          "type": "MPPT_FAILURE",
          "severity": "warning",
          "physics_confidence": 0.88,
          "message": "...",
          "reasoning": {
            "root_cause": "...",
            "recommended_actions": ["..."],
            "urgency": "Respond within 24 hours"
          }
        }
      ]
    }

  verdict values:
    HEALTHY   — no violations
    WARNING   — at least one warning-level violation, no critical
    CRITICAL  — at least one critical violation (data blocked upstream)

  data_quality values:
    CLEAN     — passed all physics checks
    FLAGGED   — forwarded to downstream with anomaly tag
    BLOCKED   — withheld from downstream AI to prevent error propagation


────────────────────────────────────────────────────────────────────────────────
  9. ANOMALY TYPES & SEVERITY
────────────────────────────────────────────────────────────────────────────────

  Rule ID    Anomaly Type      Severity   Description
  ─────────────────────────────────────────────────────────────────────────────
  PV-000-NEG SENSOR_FAULT      warning    Negative power reading
  PV-001     MPPT_FAILURE      warning    Power far below irradiance expectation
  PV-001     OVER_POWER        critical   Power exceeds inverter nameplate rating
  PV-002     OVERVOLTAGE       critical   DC bus voltage above Voc × 1.05
  PV-002     UNDERVOLTAGE      warning    DC bus voltage below Voc × 0.25
  PV-003     STRING_SHADING    warning    Current dropped >35% with stable irr.
  PV-004     THERMAL_LIMIT     warning    Module temperature above 85°C
  PV-005     DROPOUT           critical   Zero output during daytime (irr>50 W/m²)

  Confidence scoring formula:
    final = (physics_confidence × 0.6) + (type_baseline × 0.4) + learned_adj
    clamped to [0.50, 0.99]


────────────────────────────────────────────────────────────────────────────────
  10. COMMON PROBLEMS & SOLUTIONS
────────────────────────────────────────────────────────────────────────────────

  Problem: "command not found: uvicorn" after installation
  ────────────────────────────────────────────────────────
  The virtual environment is not active. Run:
    source .venv/bin/activate
  Then try again.

  Problem: "ModuleNotFoundError: No module named 'fastapi'"
  ─────────────────────────────────────────────────────────
  Same cause — activate the venv (see above), then:
    pip install -r requirements.txt

  Problem: Archive data not found when running load_archive.py
  ─────────────────────────────────────────────────────────────
  Check that the four CSV files exist at ~/Desktop/archive/.
  You can verify with:
    ls ~/Desktop/archive/
  If they are in a different location, edit the ARCHIVE variable in
  data/load_archive.py (line 36).

  Problem: AI reasoning returns rule_based instead of ai_api
  ──────────────────────────────────────────────────────────
  The ANTHROPIC_API_KEY environment variable is not set, or the key is
  invalid. The system works fine without it — rule-based reasoning is
  complete and deterministic. To enable AI reasoning:
    export ANTHROPIC_API_KEY="sk-ant-..."
  Then restart the uvicorn server.

  Problem: curl returns "Connection refused"
  ──────────────────────────────────────────
  The server is not running. Start it first:
    uvicorn api.main:app --reload --port 8000

  Problem: HTTP 422 Unprocessable Entity from /analyse
  ─────────────────────────────────────────────────────
  One or more fields failed validation. Common causes:
    • power_kw is more than 2× capacity_kw  (sanity check)
    • timestamp is not ISO-8601 format  (use "2024-01-15T10:30:00")
    • inverter_id contains a "/" character
    • a numeric field is outside its allowed range (see section 8.1)
  The response body will contain a "detail" field explaining exactly
  which field failed and why.

  Problem: /inverters/{id}/stats returns 404
  ──────────────────────────────────────────
  The alert store is in memory and resets when the server restarts.
  You need to call /analyse at least once for that inverter_id in the
  current server session before stats are available.

  Problem: test_reasoning_engine.py fails with FileNotFoundError
  ──────────────────────────────────────────────────────────────
  That test suite reads real archive CSV files. Ensure:
    ~/Desktop/archive/Plant_2_Generation_Data.csv   (exists)
    ~/Desktop/archive/Plant_2_Weather_Sensor_Data.csv  (exists)


────────────────────────────────────────────────────────────────────────────────
  QUICK REFERENCE — Start to first API call in 60 seconds
────────────────────────────────────────────────────────────────────────────────

  cd ~/Desktop/guardian_p
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  uvicorn api.main:app --reload --port 8000

  # In a new Terminal tab:
  curl -s -X POST http://127.0.0.1:8000/analyse \
    -H "Content-Type: application/json" \
    -d '{"timestamp":"2024-01-15T10:30:00","inverter_id":"inv-01",
         "power_kw":850,"irradiance_wm2":780,"voltage_v":320,
         "current_a":2.65,"temperature_c":42,"capacity_kw":1500}'

================================================================================
  Guardian P  |  core: physics_engine + reasoning_engine + feedback_loop
  API: FastAPI + Pydantic v2  |  Python 3.11+
================================================================================
