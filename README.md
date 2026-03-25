# Guardian P

> **The AI-powered quality gate for solar energy data — catching faults before they cost you.**

Solar farms lose millions in revenue every year not because panels break, but because bad data goes undetected. Faulty sensors, inverter glitches, and shading events silently corrupt the datasets that operators and AI models rely on to make decisions.

**Guardian P fixes that.**

---

## The Problem

The global solar market is projected to exceed **$500B by 2030**. But as fleets scale, data quality becomes the silent killer:

- A single miscalibrated sensor can skew an entire site's performance model
- Operators manually reviewing thousands of data points per day miss anomalies
- Downstream AI models trained on dirty data make bad predictions — and nobody knows why

---

## What Guardian P Does

Guardian P sits between your inverters and your data pipeline. Every reading passes through two layers of intelligence before it reaches your models:

**Layer 1 — Physics Engine**
Eight physics-based rules check every data point against what's physically possible given real-world conditions (irradiance, voltage, temperature). No ML required. No black box. Just hard science.

**Layer 2 — AI Reasoning (powered by Claude)**
When an anomaly is detected, Claude explains *why* in plain English, ranks urgency, and recommends specific actions — so operators know exactly what to do next.

**Layer 3 — Self-Learning**
Operators mark false positives. The system learns. Confidence scores adjust automatically and persist across restarts. The longer it runs, the smarter it gets.

---

## Traction

- Processes **136,000+ real inverter readings in ~3 seconds**
- **77 tests, all passing** across physics, reasoning, and data ingestion
- Detects 8 anomaly classes: sensor faults, MPPT failures, overvoltage, shading, thermal limits, dropouts, and more
- REST API ready — plug into any existing SCADA or data pipeline in minutes

---

## Why Now

Three forces are converging:

1. **Solar capacity is exploding** — more inverters means more data, more noise, more risk
2. **AI is eating energy ops** — but AI is only as good as its training data
3. **Regulators are tightening** — grid operators increasingly require data provenance and anomaly logging

Guardian P is infrastructure for the AI-powered grid.

---

## Quick Start

```bash
git clone https://github.com/Ike-2/guardian-p.git
cd guardian-p
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional: enable Claude AI explanations
export ANTHROPIC_API_KEY="sk-ant-..."

uvicorn api.main:app --reload --port 8000
```

Send your first reading:

```bash
curl -s -X POST http://127.0.0.1:8000/analyse \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2024-01-15T10:30:00",
    "inverter_id": "inv-01",
    "power_kw": 10,
    "irradiance_wm2": 800,
    "voltage_v": 320,
    "current_a": 2.65,
    "temperature_c": 42,
    "capacity_kw": 1500
  }'
```

Interactive API docs at `http://127.0.0.1:8000/docs`

---

## API at a Glance

| Endpoint | What it does |
|---|---|
| `POST /analyse` | Check a single inverter reading |
| `POST /analyse/batch` | Check up to 1,000 readings at once |
| `POST /feedback` | Flag a false positive — triggers self-learning |
| `GET /learning/state` | See how confidence scores have evolved |
| `GET /inverters/{id}/stats` | Per-inverter anomaly history |
| `GET /alerts` | Query all alerts with filters |

---

## Tech Stack

- **Python 3.11** / FastAPI / Uvicorn
- **Claude (Anthropic)** for AI reasoning — gracefully degrades to rule-based if no API key
- Zero external database dependencies — runs anywhere

---

## License

MIT — use it, fork it, build on it.
