# Guardian P

**Solar PV Anomaly Detection System**

Guardian P monitors solar inverter output in real time. It combines a physics-constraint engine with an optional Claude AI reasoning layer to detect, classify, and explain anomalies before bad data reaches downstream models.

## Core Features

- **Physics Constraint Engine** — 8 rule-based checks (PV-000 through PV-005) covering sensor faults, MPPT failures, over/under-voltage, shading, thermal limits, and dropouts
- **AI Reasoning Layer** — Optional Claude-powered natural-language explanations with automatic rule-based fallback
- **Self-Learning Feedback Loop** — Operator feedback adjusts confidence scores over time, persisted across restarts
- **Batch Processing** — Analyse up to 1,000 data points per request, or ingest full archive CSVs (136k+ rows in ~3s)
- **REST API** — FastAPI with auto-generated Swagger/ReDoc docs, request tracing, and per-inverter statistics

## Test Results

| Suite | Tests | Status |
|---|---|---|
| `test_physics_engine.py` | 13 | Passed |
| `test_reasoning_engine.py` | 23 | Passed |
| `test_load_archive.py` | 41 | Passed |
| **Total** | **77** | **All Passed** |

## Quick Start

```bash
# Clone and enter the project
cd guardian_p

# Create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Enable AI reasoning
export ANTHROPIC_API_KEY="sk-ant-..."

# Start the API server
uvicorn api.main:app --reload --port 8000
```

Then in a new terminal:

```bash
curl -s -X POST http://127.0.0.1:8000/analyse \
  -H "Content-Type: application/json" \
  -d '{"timestamp":"2024-01-15T10:30:00","inverter_id":"inv-01",
       "power_kw":850,"irradiance_wm2":780,"voltage_v":320,
       "current_a":2.65,"temperature_c":42,"capacity_kw":1500}'
```

## Project Structure

```
guardian_p/
├── api/
│   └── main.py                 # FastAPI application (all HTTP endpoints)
├── core/
│   ├── physics_engine.py       # Physics constraint rules (PV-001 … PV-005)
│   ├── reasoning_engine.py     # Claude AI reasoning layer + rule-based fallback
│   └── feedback_loop.py        # Self-learning feedback persistence
├── data/
│   └── load_archive.py         # CSV ingestion with auto-detection heuristics
├── tests/
│   ├── test_physics_engine.py   # 13 unit tests
│   ├── test_reasoning_engine.py # 23 unit tests
│   └── test_load_archive.py     # 41 unit tests
├── requirements.txt
├── LICENSE
└── README.md
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/analyse` | Analyse a single inverter data point |
| `POST` | `/analyse/batch` | Analyse up to 1,000 data points |
| `POST` | `/feedback` | Submit operator feedback (self-learning) |
| `GET` | `/learning/state` | View current confidence adjustments |
| `GET` | `/inverters/{id}/stats` | Per-inverter anomaly statistics |
| `GET` | `/alerts` | Query stored alerts with filters |
| `POST` | `/diagnose` | Read-only diagnosis (no side effects) |

Interactive docs available at `http://127.0.0.1:8000/docs` (Swagger UI) and `/redoc`.

## Anomaly Types

| Rule ID | Type | Severity | Description |
|---|---|---|---|
| PV-000-NEG | `SENSOR_FAULT` | warning | Negative power reading |
| PV-001 | `MPPT_FAILURE` | warning | Power far below irradiance expectation |
| PV-001 | `OVER_POWER` | critical | Power exceeds inverter nameplate rating |
| PV-002 | `OVERVOLTAGE` | critical | DC bus voltage above Voc × 1.05 |
| PV-002 | `UNDERVOLTAGE` | warning | DC bus voltage below Voc × 0.25 |
| PV-003 | `STRING_SHADING` | warning | Current dropped >35% with stable irradiance |
| PV-004 | `THERMAL_LIMIT` | warning | Module temperature above 85°C |
| PV-005 | `DROPOUT` | critical | Zero output during daytime (irr > 50 W/m²) |

## Configuration

**AI Reasoning (optional):** Set `ANTHROPIC_API_KEY` to enable Claude-powered explanations. Without it, the system uses complete rule-based reasoning — no functionality is lost.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

**Archive Data:** Batch mode expects CSV files at `~/Desktop/archive/`. Edit the `ARCHIVE` variable in `data/load_archive.py` if your files are elsewhere.

## Running Tests

```bash
# Using unittest (no extra dependencies)
python3 tests/test_physics_engine.py
python3 tests/test_reasoning_engine.py
python3 tests/test_load_archive.py

# Or with pytest
python3 -m pytest tests/ -v
```

> **Note:** `test_reasoning_engine.py` requires archive CSV files at `~/Desktop/archive/`.

## Requirements

- Python 3.11+
- FastAPI 0.111.0
- Uvicorn 0.29.0
- Pydantic 2.7.1
- Anthropic SDK 0.28.0 (optional)

## License

This project is licensed under the [MIT License](LICENSE).
