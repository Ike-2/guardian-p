"""
Guardian P — FastAPI Application
==================================
REST API exposing Guardian P's physics engine, reasoning layer,
and feedback loop. Deploy with: uvicorn api.main:app --reload
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Literal
from datetime import datetime
from collections import defaultdict
import logging
import time
import uuid
import uvicorn

from core.physics_engine import DataPoint, analyse, AnomalyType, Severity
from core.reasoning_engine import ReasoningEngine
from core.feedback_loop import FeedbackLoop

# ── LOGGING SETUP ───────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("guardian_p.api")


# ── APP SETUP ──────────────────────────────────────────────────────────────
app = FastAPI(
    title       = "Guardian P",
    description = "Energy Data Intelligence Middleware — Physics-Constrained Anomaly Detection API",
    version     = "1.0.0",
    docs_url    = "/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)


# ── REQUEST TIMING & OBSERVABILITY MIDDLEWARE ───────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    For every HTTP request:
    • Generate a unique request_id and attach it to the response header.
    • Log method, path, status code, and wall-clock duration_ms.
    • On unhandled exceptions return a structured 500 with the request_id
      so operators can correlate client errors with server logs.
    """
    request_id = str(uuid.uuid4())[:8]
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Duration-Ms"] = str(duration_ms)
        logger.info(
            "%s  %s %s  status=%d  duration=%sms",
            request_id, request.method, request.url.path,
            response.status_code, duration_ms,
        )
        return response
    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.error(
            "%s  %s %s  UNHANDLED EXCEPTION after %sms: %s",
            request_id, request.method, request.url.path, duration_ms, exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code = 500,
            content     = {
                "error":      "internal_server_error",
                "request_id": request_id,
                "detail":     "An unexpected error occurred. Check server logs.",
            },
            headers = {"X-Request-ID": request_id},
        )

# Initialise engines (shared across requests)
reasoning = ReasoningEngine(use_ai=False)   # Set use_ai=True when ANTHROPIC_API_KEY is set
feedback_loop = FeedbackLoop(reasoning_engine=reasoning)

# In-memory store for last data point per inverter (for stateful rules)
_last_points: dict[str, DataPoint] = {}

# ── ALERT HISTORY STORE ─────────────────────────────────────────────────────
# Keyed by inverter_id → list of alert records (newest appended last).
# Each record is a plain dict so it's instantly JSON-serialisable.
# In production replace with a time-series DB (TimescaleDB, InfluxDB, etc.).

_alert_history: dict[str, list[dict]] = defaultdict(list)


def _store_alert(inverter_id: str, timestamp: str, anomaly_types: list[str],
                 severities: list[str], alert_id: str, is_blocked: bool,
                 violations: list[dict]) -> None:
    """Append a normalised alert record to the in-memory history store."""
    _alert_history[inverter_id].append({
        "alert_id":      alert_id,
        "inverter_id":   inverter_id,
        "timestamp":     timestamp,
        "anomaly_types": anomaly_types,
        "severities":    severities,
        "is_blocked":    is_blocked,
        "violations":    violations,
    })


# ── REQUEST / RESPONSE SCHEMAS ─────────────────────────────────────────────

class DataPointRequest(BaseModel):
    timestamp:      str   = Field(..., example="2025-06-01T10:30:00Z")
    inverter_id:    str   = Field(..., min_length=1, max_length=128, example="INV-001")
    power_kw:       float = Field(..., ge=0, le=100_000, example=72.5)
    irradiance_wm2: float = Field(..., ge=0, le=1400,    example=750.0)
    voltage_v:      float = Field(..., ge=0, le=5000,    example=385.0)
    current_a:      float = Field(..., ge=0, le=100_000, example=187.5)
    temperature_c:  float = Field(..., ge=-60, le=200,   example=42.3)
    capacity_kw:    float = Field(100.0, ge=1, le=1_000_000, example=100.0)
    voc:            float = Field(400.0, ge=1, le=5000,  example=400.0)
    isc:            float = Field(10.0,  ge=0, le=10_000, example=10.0)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Reject timestamps that cannot be parsed — they would silently miss weather joins."""
        v = v.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                datetime.strptime(v.rstrip("Z"), fmt.rstrip("Z"))
                return v
            except ValueError:
                continue
        raise ValueError(
            f"timestamp {v!r} is not a recognised ISO-8601 format. "
            "Use e.g. '2025-06-01T10:30:00' or '2025-06-01T10:30:00Z'."
        )

    @field_validator("inverter_id")
    @classmethod
    def validate_inverter_id(cls, v: str) -> str:
        """Reject inverter IDs containing path separators that could break URL routing."""
        if "/" in v:
            raise ValueError(
                f"inverter_id {v!r} must not contain '/'. "
                "Use '::' as a plant::inverter separator instead."
            )
        return v.strip()

    @model_validator(mode="after")
    def power_vs_capacity_sanity(self) -> "DataPointRequest":
        """
        Soft sanity check: power_kw > capacity_kw × 2 is almost certainly a
        unit mismatch (e.g. W submitted instead of kW).  Reject early with a
        clear message rather than letting the physics engine flag it silently.
        """
        if self.power_kw > self.capacity_kw * 2:
            raise ValueError(
                f"power_kw ({self.power_kw}) is more than 2× capacity_kw ({self.capacity_kw}). "
                "This is likely a unit error (W submitted instead of kW). "
                "If intentional, increase capacity_kw to match the inverter nameplate."
            )
        return self


class FeedbackRequest(BaseModel):
    alert_id:          str  = Field(..., min_length=1, example="a1b2c3d4")
    inverter_id:       str  = Field(..., min_length=1, example="INV-001")
    anomaly_type:      str  = Field(..., example="MPPT_FAILURE")
    is_false_positive: bool = Field(..., example=False)
    operator_note:     Optional[str] = Field(None, max_length=1000,
                           example="Confirmed — string 3 disconnected")

    @field_validator("anomaly_type")
    @classmethod
    def validate_anomaly_type(cls, v: str) -> str:
        valid = {a.value for a in AnomalyType}
        if v not in valid:
            raise ValueError(
                f"anomaly_type {v!r} is not recognised. "
                f"Valid values: {sorted(valid)}"
            )
        return v


class BatchRequest(BaseModel):
    readings: list[DataPointRequest] = Field(..., min_length=1, max_length=500)


# ── ENDPOINTS ──────────────────────────────────────────────────────────────

@app.get("/", tags=["Status"])
def root():
    return {
        "service": "Guardian P",
        "version": "1.0.0",
        "status":  "online",
        "docs":    "/docs",
    }


@app.get("/health", tags=["Status"])
def health():
    stats = feedback_loop.get_stats()
    return {
        "status":            "healthy",
        "physics_rules":     5,
        "feedback_records":  stats.total_feedback,
        "model_precision":   stats.precision,
        "ai_reasoning":      reasoning.use_ai,
    }


@app.post("/analyse", tags=["Core"])
def analyse_point(req: DataPointRequest):
    """
    Analyse a single inverter data point.
    Guardian P runs physics constraint rules and returns:
    - is_clean: whether the data point passed all checks
    - is_blocked: whether it has been withheld from downstream AI
    - alert_package: full reasoning output if anomalies were detected
    """
    dp = DataPoint(
        timestamp      = req.timestamp,
        inverter_id    = req.inverter_id,
        power_kw       = req.power_kw,
        irradiance_wm2 = req.irradiance_wm2,
        voltage_v      = req.voltage_v,
        current_a      = req.current_a,
        temperature_c  = req.temperature_c,
        capacity_kw    = req.capacity_kw,
        voc            = req.voc,
        isc            = req.isc,
    )

    prev_dp = _last_points.get(req.inverter_id)
    result  = analyse(dp, prev_dp)
    _last_points[req.inverter_id] = dp

    if result.is_clean:
        return {
            "status":        "clean",
            "is_clean":      True,
            "is_blocked":    False,
            "inverter_id":   req.inverter_id,
            "timestamp":     req.timestamp,
            "alert_package": None,
        }

    package = reasoning.process(result)

    # Persist to history store for later querying
    _store_alert(
        inverter_id   = req.inverter_id,
        timestamp     = req.timestamp,
        anomaly_types = [v["type"] for v in package.raw_violations],
        severities    = [v["severity"] for v in package.raw_violations],
        alert_id      = package.alert_id,
        is_blocked    = package.is_blocked,
        violations    = package.raw_violations,
    )

    return {
        "status":      "anomaly_detected",
        "is_clean":    False,
        "is_blocked":  package.is_blocked,
        "inverter_id": req.inverter_id,
        "timestamp":   req.timestamp,
        "alert_package": {
            "alert_id":   package.alert_id,
            "is_blocked": package.is_blocked,
            "violations": len(package.raw_violations),
            "reasoning":  [
                {
                    "anomaly_type":         r.anomaly_type,
                    "severity":             r.severity,
                    "confidence":           r.confidence_score,
                    "root_cause":           r.root_cause,
                    "supporting_facts":     r.supporting_facts,
                    "recommended_actions":  r.recommended_actions,
                    "urgency":              r.urgency,
                    "clean_data_action":    r.clean_data_action,
                    "source":               r.reasoning_source,
                }
                for r in package.reasoning
            ],
        },
    }


@app.post("/analyse/batch", tags=["Core"])
def analyse_batch(req: BatchRequest):
    """
    Analyse up to 500 data points in one request.
    Useful for processing historical CSV data or catching up after downtime.
    Returns summary statistics + individual results.
    """
    results     = []
    clean_count = 0
    blocked_count = 0

    for reading in req.readings:
        dp = DataPoint(
            timestamp      = reading.timestamp,
            inverter_id    = reading.inverter_id,
            power_kw       = reading.power_kw,
            irradiance_wm2 = reading.irradiance_wm2,
            voltage_v      = reading.voltage_v,
            current_a      = reading.current_a,
            temperature_c  = reading.temperature_c,
            capacity_kw    = reading.capacity_kw,
            voc            = reading.voc,
            isc            = reading.isc,
        )

        prev_dp = _last_points.get(reading.inverter_id)
        result  = analyse(dp, prev_dp)
        _last_points[reading.inverter_id] = dp

        if result.is_clean:
            clean_count += 1
            results.append({"inverter_id": reading.inverter_id, "timestamp": reading.timestamp, "status": "clean"})
        else:
            package = reasoning.process(result)
            if package.is_blocked:
                blocked_count += 1
            # Persist to history store
            _store_alert(
                inverter_id   = reading.inverter_id,
                timestamp     = reading.timestamp,
                anomaly_types = [v["type"] for v in package.raw_violations],
                severities    = [v["severity"] for v in package.raw_violations],
                alert_id      = package.alert_id,
                is_blocked    = package.is_blocked,
                violations    = package.raw_violations,
            )
            results.append({
                "inverter_id": reading.inverter_id,
                "timestamp":   reading.timestamp,
                "status":      "anomaly_detected",
                "is_blocked":  package.is_blocked,
                "alert_id":    package.alert_id,
                "anomaly_types": [v["type"] for v in package.raw_violations],
            })

    total = len(req.readings)
    return {
        "summary": {
            "total":     total,
            "clean":     clean_count,
            "anomalies": total - clean_count,
            "blocked":   blocked_count,
            "clean_rate": round(clean_count / total * 100, 1),
        },
        "results": results,
    }


@app.post("/feedback", tags=["Self-Learning"])
def submit_feedback(req: FeedbackRequest):
    """
    Submit operator feedback on an alert.
    Guardian P uses this to automatically recalibrate detection confidence.
    False positives reduce sensitivity; confirmed alerts increase it.
    """
    result = feedback_loop.submit(
        alert_id          = req.alert_id,
        inverter_id       = req.inverter_id,
        anomaly_type      = req.anomaly_type,
        is_false_positive = req.is_false_positive,
        operator_note     = req.operator_note,
    )
    return result


@app.get("/learning/state", tags=["Self-Learning"])
def get_learning_state():
    """
    Returns current confidence baselines and learned adjustments
    for all anomaly types. Shows how the model has evolved from feedback.
    """
    stats = feedback_loop.get_stats()
    return {
        "precision":        stats.precision,
        "total_feedback":   stats.total_feedback,
        "confirmed_correct": stats.confirmed_correct,
        "false_positives":  stats.false_positives,
        "confidence_state": stats.adjustments,
    }


@app.get("/inverters/{inverter_id}/stats", tags=["Analytics"])
def inverter_stats(inverter_id: str):
    """
    Anomaly statistics for a single inverter.

    Returns total alert count, breakdown by anomaly type and severity,
    the block rate, and the five most recent alerts — giving operators
    a quick health snapshot without trawling raw logs.
    """
    records = _alert_history.get(inverter_id)
    if not records:
        raise HTTPException(
            status_code = 404,
            detail      = f"No alert history found for inverter '{inverter_id}'. "
                          "Either the inverter has no anomalies or no data has been "
                          "submitted via /analyse yet.",
        )

    # Tally anomaly types and severities across all alerts
    type_counts: dict[str, int]     = defaultdict(int)
    severity_counts: dict[str, int] = defaultdict(int)
    blocked_count = 0

    for rec in records:
        for atype in rec["anomaly_types"]:
            type_counts[atype] += 1
        for sev in rec["severities"]:
            severity_counts[sev] += 1
        if rec["is_blocked"]:
            blocked_count += 1

    total = len(records)
    return {
        "inverter_id":    inverter_id,
        "total_alerts":   total,
        "blocked_alerts": blocked_count,
        "block_rate_pct": round(blocked_count / total * 100, 1),
        "by_anomaly_type": dict(sorted(type_counts.items(), key=lambda x: -x[1])),
        "by_severity":     dict(sorted(severity_counts.items(), key=lambda x: -x[1])),
        "recent_alerts":   records[-5:][::-1],   # last 5, newest first
    }


@app.get("/alerts", tags=["Analytics"])
def list_alerts(
    inverter_id:  Optional[str] = Query(None, description="Filter by inverter ID (partial match, case-insensitive)"),
    start:        Optional[str] = Query(None, description="Start of time window — ISO-8601, e.g. 2020-05-15T06:00:00"),
    end:          Optional[str] = Query(None, description="End of time window   — ISO-8601, e.g. 2020-05-15T18:00:00"),
    anomaly_type: Optional[str] = Query(None, description=f"Filter by anomaly type. Valid: {[a.value for a in AnomalyType]}"),
    severity:     Optional[str] = Query(None, description="Filter by severity: critical | warning | info"),
    limit:        int           = Query(100, ge=1, le=1000, description="Max results (default 100, max 1000)"),
):
    """
    Query alert history with flexible filters.

    All parameters are optional and combinable:
    - **inverter_id** — substring match (case-insensitive)
    - **start / end** — ISO-8601 timestamps; either or both can be omitted
    - **anomaly_type** — must be a valid AnomalyType value
    - **severity** — must be one of: critical, warning, info
    - **limit** — cap results (newest-first after filtering)
    """
    # Validate enum-like query params early with clear 422 messages
    valid_anomaly_types = {a.value for a in AnomalyType}
    if anomaly_type and anomaly_type.upper() not in valid_anomaly_types:
        raise HTTPException(
            status_code = 422,
            detail      = f"anomaly_type {anomaly_type!r} is not valid. "
                          f"Choose from: {sorted(valid_anomaly_types)}",
        )
    valid_severities = {"critical", "warning", "info"}
    if severity and severity.lower() not in valid_severities:
        raise HTTPException(
            status_code = 422,
            detail      = f"severity {severity!r} is not valid. "
                          f"Choose from: {sorted(valid_severities)}",
        )
    # Parse time bounds once, outside the loop
    try:
        dt_start = datetime.fromisoformat(start) if start else None
        dt_end   = datetime.fromisoformat(end)   if end   else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid datetime format: {exc}")

    matched: list[dict] = []

    for inv_id, records in _alert_history.items():
        # Inverter filter (case-insensitive substring)
        if inverter_id and inverter_id.lower() not in inv_id.lower():
            continue

        for rec in records:
            # Time window filter
            try:
                rec_dt = datetime.fromisoformat(rec["timestamp"])
            except ValueError:
                continue
            if dt_start and rec_dt < dt_start:
                continue
            if dt_end   and rec_dt > dt_end:
                continue

            # Anomaly type filter (any violation in the alert matches)
            if anomaly_type and anomaly_type.upper() not in [t.upper() for t in rec["anomaly_types"]]:
                continue

            # Severity filter
            if severity and severity.lower() not in [s.lower() for s in rec["severities"]]:
                continue

            matched.append(rec)

    # Sort newest-first then apply limit
    matched.sort(key=lambda r: r["timestamp"], reverse=True)
    matched = matched[:limit]

    return {
        "total_matched": len(matched),
        "filters_applied": {
            "inverter_id":  inverter_id,
            "start":        start,
            "end":          end,
            "anomaly_type": anomaly_type,
            "severity":     severity,
        },
        "alerts": matched,
    }


@app.post("/diagnose", tags=["Analytics"])
def diagnose(req: DataPointRequest):
    """
    Real-time diagnosis endpoint — like /analyse but richer output.

    Returns the full physics violation detail alongside the reasoning
    package, plus a plain-English summary and a data quality verdict.
    Intended for dashboard "Diagnose Now" buttons and live SCADA feeds.

    The data point is NOT persisted to the alert history store, so it
    won't skew historical statistics.  Use /analyse for ingestion.
    """
    dp = DataPoint(
        timestamp      = req.timestamp,
        inverter_id    = req.inverter_id,
        power_kw       = req.power_kw,
        irradiance_wm2 = req.irradiance_wm2,
        voltage_v      = req.voltage_v,
        current_a      = req.current_a,
        temperature_c  = req.temperature_c,
        capacity_kw    = req.capacity_kw,
        voc            = req.voc,
        isc            = req.isc,
    )

    # Use the last known point for this inverter (stateful current-drop rule)
    prev_dp = _last_points.get(req.inverter_id)
    result  = analyse(dp, prev_dp)
    # Note: intentionally NOT updating _last_points — diagnose is read-only

    if result.is_clean:
        return {
            "verdict":      "HEALTHY",
            "summary":      f"All physics checks passed for {req.inverter_id} at {req.timestamp}.",
            "inverter_id":  req.inverter_id,
            "timestamp":    req.timestamp,
            "violations":   [],
            "reasoning":    [],
            "data_quality": "PASS — safe to use in downstream AI models.",
        }

    package = reasoning.process(result)

    # Build a human-readable one-line summary
    worst = result.worst_severity.value.upper() if result.worst_severity else "UNKNOWN"
    types = ", ".join(sorted({v.anomaly_type.value for v in result.violations}))
    summary = (
        f"{worst} anomaly detected on {req.inverter_id} at {req.timestamp}: {types}."
    )

    data_quality = (
        "BLOCKED — reading withheld from downstream AI (critical violation present)."
        if package.is_blocked
        else "FLAGGED — forwarded with reduced confidence weight."
    )

    return {
        "verdict":      worst,
        "summary":      summary,
        "inverter_id":  req.inverter_id,
        "timestamp":    req.timestamp,
        "violations": [
            {
                "rule_id":       v.rule_id,
                "anomaly_type":  v.anomaly_type.value,
                "severity":      v.severity.value,
                "measured":      v.measured,
                "expected_min":  v.expected_min,
                "expected_max":  v.expected_max,
                "physics_confidence": v.confidence,
                "message":       v.message,
            }
            for v in result.violations
        ],
        "reasoning": [
            {
                "anomaly_type":        r.anomaly_type,
                "severity":            r.severity,
                "confidence":          r.confidence_score,
                "root_cause":          r.root_cause,
                "supporting_facts":    r.supporting_facts,
                "recommended_actions": r.recommended_actions,
                "urgency":             r.urgency,
                "source":              r.reasoning_source,
            }
            for r in package.reasoning
        ],
        "data_quality": data_quality,
    }


# ── DEV RUNNER ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
