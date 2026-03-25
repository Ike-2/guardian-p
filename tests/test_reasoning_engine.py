"""
Guardian P — Reasoning Engine Test Suite
=========================================
Evaluates the ReasoningEngine against real archive anomalies and synthetic
edge-case scenarios.

Test dimensions
---------------
1. Rule-based mode (use_ai=False) — deterministic, always runs offline
2. Output contract — all required fields are present and have valid types/ranges
3. Confidence calibration — blending, self-learning feedback, clamping
4. Classification quality — root_cause relevance, action completeness
5. Graceful degradation — unknown anomaly types, empty violations
6. Feedback loop — precision tracking, cumulative delta bounds

Run:
    cd ~/Desktop/guardian_p
    python tests/test_reasoning_engine.py
    # or with pytest:
    python -m pytest tests/test_reasoning_engine.py -v
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.physics_engine import (
    DataPoint, analyse, AnomalyType, Severity, PhysicsViolation,
)
from core.reasoning_engine import (
    ReasoningEngine, AlertPackage, ReasoningOutput, CONFIDENCE_BASELINES,
)

# ── Colours for terminal output ────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES — DataPoints derived from real archive samples
# ══════════════════════════════════════════════════════════════════════════

def _dp(power, irr, volt=320, curr=None, temp=45, cap=1522.8, inv="plant_2::TEST", ts="2020-05-15T09:30:00"):
    if curr is None:
        curr = power * 1000 / volt if power > 0 else 0.0
    return DataPoint(
        timestamp      = ts,
        inverter_id    = inv,
        power_kw       = power,
        irradiance_wm2 = irr,
        voltage_v      = volt,
        current_a      = curr,
        temperature_c  = temp,
        capacity_kw    = cap,
        voc            = 400.0,
        isc            = 10.0,
    )

# Real-data fixtures (values extracted from archive analysis)
FIXTURE_MPPT     = _dp(power=0.0,    irr=799.7, temp=45.7)   # plant_2, 2020-05-15T09:45
FIXTURE_SHADING  = _dp(power=165.6,  irr=756.7, curr=528.7)  # plant_2, 2020-05-15T09:30 (Quc1)
FIXTURE_OVERP    = _dp(power=1138.2, irr=682.6, temp=54.7)   # plant_2, 2020-05-16T13:15
FIXTURE_NORMAL   = _dp(power=1100.0, irr=850.0, temp=42.0)   # healthy reference
FIXTURE_THERMAL  = _dp(power=900.0,  irr=800.0, temp=91.0)   # above IEC limit
FIXTURE_DROPOUT  = _dp(power=0.0,    irr=700.0, volt=1.0, curr=0.0)  # complete dropout
FIXTURE_SENSOR   = _dp(power=800.0,  irr=500.0, temp=-20.0)  # physically impossible cold


# ══════════════════════════════════════════════════════════════════════════
# TEST INFRASTRUCTURE
# ══════════════════════════════════════════════════════════════════════════

results: list[dict] = []

def run_test(name: str, fn):
    """Execute one test function, capture pass/fail, print result."""
    t0 = time.time()
    try:
        fn()
        elapsed = (time.time() - t0) * 1000
        results.append({"name": name, "status": "PASS", "ms": elapsed})
        print(f"  {GREEN}✓{RESET}  {name}  {YELLOW}({elapsed:.0f}ms){RESET}")
    except AssertionError as e:
        elapsed = (time.time() - t0) * 1000
        results.append({"name": name, "status": "FAIL", "ms": elapsed, "error": str(e)})
        print(f"  {RED}✗{RESET}  {name}")
        print(f"      {RED}AssertionError: {e}{RESET}")
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        results.append({"name": name, "status": "ERROR", "ms": elapsed, "error": str(e)})
        print(f"  {RED}!{RESET}  {name}")
        print(f"      {RED}{type(e).__name__}: {e}{RESET}")


def _engine(use_ai=False) -> ReasoningEngine:
    return ReasoningEngine(use_ai=use_ai)


def _analyse_and_process(dp: DataPoint, prev=None, engine=None) -> AlertPackage:
    if engine is None:
        engine = _engine()
    result = analyse(dp, prev)
    assert not result.is_clean, "Expected violations but data point was clean"
    return engine.process(result)


# ══════════════════════════════════════════════════════════════════════════
# GROUP 1 — Output contract (every field present and correctly typed)
# ══════════════════════════════════════════════════════════════════════════

def test_alert_package_fields():
    """AlertPackage must have all required top-level fields with correct types."""
    pkg = _analyse_and_process(FIXTURE_MPPT)
    assert isinstance(pkg.alert_id,       str)  and len(pkg.alert_id) > 0
    assert isinstance(pkg.timestamp,      str)  and len(pkg.timestamp) > 0
    assert isinstance(pkg.inverter_id,    str)
    assert isinstance(pkg.is_blocked,     bool)
    assert isinstance(pkg.reasoning,      list) and len(pkg.reasoning) > 0
    assert isinstance(pkg.raw_violations, list) and len(pkg.raw_violations) > 0


def test_reasoning_output_fields():
    """Every ReasoningOutput must carry all required fields with correct types."""
    pkg = _analyse_and_process(FIXTURE_MPPT)
    for ro in pkg.reasoning:
        assert isinstance(ro.anomaly_type,       str)
        assert isinstance(ro.severity,           str)
        assert isinstance(ro.confidence_score,   float)
        assert isinstance(ro.root_cause,         str)  and len(ro.root_cause) > 10
        assert isinstance(ro.supporting_facts,   list) and len(ro.supporting_facts) >= 1
        assert isinstance(ro.recommended_actions,list) and len(ro.recommended_actions) >= 1
        assert isinstance(ro.urgency,            str)  and len(ro.urgency) > 0
        assert isinstance(ro.clean_data_action,  str)  and len(ro.clean_data_action) > 0
        assert isinstance(ro.reasoning_source,   str)


def test_raw_violations_fields():
    """raw_violations must contain all serialisable keys needed by downstream consumers."""
    pkg = _analyse_and_process(FIXTURE_OVERP)
    required = {"type","severity","rule_id","message","measured","expected_min","expected_max","confidence"}
    for v in pkg.raw_violations:
        missing = required - set(v.keys())
        assert not missing, f"raw_violations entry missing keys: {missing}"


def test_alert_id_unique_per_call():
    """Each call to process() must produce a distinct alert_id."""
    engine = _engine()
    result = analyse(FIXTURE_MPPT)
    ids = {engine.process(result).alert_id for _ in range(5)}
    assert len(ids) == 5, f"Expected 5 unique IDs, got {len(ids)}: {ids}"


# ══════════════════════════════════════════════════════════════════════════
# GROUP 2 — Confidence calibration
# ══════════════════════════════════════════════════════════════════════════

def test_confidence_within_bounds():
    """All confidence scores must be in [0.50, 0.99]."""
    engine = _engine()
    for fixture in (FIXTURE_MPPT, FIXTURE_OVERP, FIXTURE_THERMAL, FIXTURE_DROPOUT):
        result = analyse(fixture)
        if result.is_clean:
            continue
        pkg = engine.process(result)
        for ro in pkg.reasoning:
            assert 0.50 <= ro.confidence_score <= 0.99, (
                f"{ro.anomaly_type}: confidence {ro.confidence_score} out of [0.50, 0.99]"
            )


def test_confidence_blending_formula():
    """
    Confidence = 0.6 × physics_conf + 0.4 × baseline (no adjustment).
    Verify the formula is applied correctly for MPPT_FAILURE.
    """
    engine = _engine()
    result = analyse(FIXTURE_MPPT)
    assert not result.is_clean
    violation = next(v for v in result.violations if v.anomaly_type == AnomalyType.MPPT_FAILURE)
    pkg = engine.process(result)
    ro  = next(r for r in pkg.reasoning if r.anomaly_type == "MPPT_FAILURE")

    physics_conf = violation.confidence
    baseline     = CONFIDENCE_BASELINES[AnomalyType.MPPT_FAILURE]
    expected     = round(max(0.50, min(0.99, physics_conf * 0.6 + baseline * 0.4)), 3)
    assert ro.confidence_score == expected, (
        f"Blending error: got {ro.confidence_score}, expected {expected} "
        f"(physics={physics_conf}, baseline={baseline})"
    )


def test_false_positive_feedback_lowers_confidence():
    """Reporting a false positive must reduce the effective confidence baseline."""
    engine  = _engine()
    before  = engine.get_learning_state()[AnomalyType.MPPT_FAILURE.value]["effective"]
    engine.apply_feedback(AnomalyType.MPPT_FAILURE, is_false_positive=True)
    after   = engine.get_learning_state()[AnomalyType.MPPT_FAILURE.value]["effective"]
    assert after < before, f"Expected confidence to drop: {before} → {after}"


def test_confirmed_feedback_raises_confidence():
    """Confirming an alert must increase effective confidence."""
    engine  = _engine()
    before  = engine.get_learning_state()[AnomalyType.STRING_SHADING.value]["effective"]
    engine.apply_feedback(AnomalyType.STRING_SHADING, is_false_positive=False)
    after   = engine.get_learning_state()[AnomalyType.STRING_SHADING.value]["effective"]
    assert after > before, f"Expected confidence to rise: {before} → {after}"


def test_feedback_cumulative_delta_clamped():
    """
    Cumulative false-positive feedback must be clamped at -0.20.
    (The engine rejects adjustments below -0.20 or above +0.10.)
    """
    engine = _engine()
    # Apply 10 false-positive signals (each is −0.04 → total would be −0.40)
    for _ in range(10):
        engine.apply_feedback(AnomalyType.MPPT_FAILURE, is_false_positive=True)
    adj = engine._learned_adjustments.get(AnomalyType.MPPT_FAILURE, 0.0)
    assert adj >= -0.20, f"Adjustment {adj} below minimum clamp −0.20"


def test_feedback_positive_delta_clamped():
    """Cumulative confirmed feedback must be clamped at +0.10."""
    engine = _engine()
    for _ in range(20):
        engine.apply_feedback(AnomalyType.THERMAL_LIMIT, is_false_positive=False)
    adj = engine._learned_adjustments.get(AnomalyType.THERMAL_LIMIT, 0.0)
    assert adj <= 0.10, f"Adjustment {adj} above maximum clamp +0.10"


# ══════════════════════════════════════════════════════════════════════════
# GROUP 3 — Classification quality (rule-based mode)
# ══════════════════════════════════════════════════════════════════════════

def test_mppt_failure_classification():
    """
    Real archive MPPT_FAILURE: zero output at 800 W/m².
    Expected: WARNING severity, ≥1 action mentioning MPPT or string.
    """
    pkg = _analyse_and_process(FIXTURE_MPPT)
    ro  = next((r for r in pkg.reasoning if r.anomaly_type == "MPPT_FAILURE"), None)
    assert ro is not None, "MPPT_FAILURE not found in reasoning output"
    assert ro.severity == "warning"
    keywords = {"mppt", "string", "soiling", "disconnect", "combiner"}
    actions_text = " ".join(ro.recommended_actions).lower()
    assert any(k in actions_text for k in keywords), (
        f"No MPPT-related keyword in actions: {ro.recommended_actions}"
    )
    assert len(ro.supporting_facts) >= 2
    assert ro.urgency != ""


def test_over_power_classification():
    """
    Real archive OVER_POWER: 1138 kW at 683 W/m² (cap 1522.8 kW).
    Expected: CRITICAL severity, data blocked, actions mention meter/CT/scaling.
    """
    pkg = _analyse_and_process(FIXTURE_OVERP)
    ro  = next((r for r in pkg.reasoning if r.anomaly_type == "OVER_POWER"), None)
    assert ro is not None, "OVER_POWER not found in reasoning output"
    assert ro.severity == "critical"
    assert pkg.is_blocked, "CRITICAL violation must block data"
    keywords = {"meter", "ct", "scaling", "logger", "firmware", "clamp", "corruption"}
    actions_text = " ".join(ro.recommended_actions).lower()
    assert any(k in actions_text for k in keywords), (
        f"No meter/CT keyword in actions: {ro.recommended_actions}"
    )


def test_string_shading_classification():
    """
    Real archive STRING_SHADING: inv 81aHJ1q11NBPMrL at 2020-05-15T09:30.
      prev: curr=2734.7 A, irr=702.4 W/m²
      curr: curr=1754.8 A, irr=756.7 W/m²  → 35.8% drop, irr_change=7.7% (<20% guard)
    Expected: WARNING severity, actions mention shading/diode/connector.
    """
    # prev_irr and curr_irr must be within 20% of each other to pass the guard
    # in rule_current_drop (irr_change > 0.20 → skip). Real values: 702→756 = 7.7%.
    prev_dp = _dp(power=876.1, irr=702.4, curr=2734.7)
    curr_dp = _dp(power=561.5, irr=756.7, curr=1754.8)
    pkg     = _analyse_and_process(curr_dp, prev=prev_dp)
    ro      = next((r for r in pkg.reasoning if r.anomaly_type == "STRING_SHADING"), None)
    assert ro is not None, "STRING_SHADING not found in reasoning output"
    assert ro.severity == "warning"
    keywords = {"shading", "diode", "connector", "bypass", "soiling", "string"}
    actions_text = " ".join(ro.recommended_actions).lower()
    assert any(k in actions_text for k in keywords), (
        f"No shading-related keyword in actions: {ro.recommended_actions}"
    )


def test_thermal_limit_classification():
    """
    Synthetic: module temp 91°C (above IEC 61215 limit of 85°C).
    Expected: CRITICAL, data blocked, actions mention cooling/ventilation.
    """
    pkg = _analyse_and_process(FIXTURE_THERMAL)
    ro  = next((r for r in pkg.reasoning if r.anomaly_type == "THERMAL_LIMIT"), None)
    assert ro is not None, "THERMAL_LIMIT not found in reasoning output"
    assert ro.severity == "critical"
    assert pkg.is_blocked
    keywords = {"cooling", "ventilation", "soiling", "hotspot", "temperature", "inspect"}
    actions_text = " ".join(ro.recommended_actions).lower()
    assert any(k in actions_text for k in keywords), (
        f"No thermal-related keyword in actions: {ro.recommended_actions}"
    )


def test_dropout_classification():
    """
    Synthetic: zero output + near-zero voltage at 700 W/m².
    Expected: CRITICAL, blocked, urgency is within-2-hours, actions mention disconnect.
    """
    pkg = _analyse_and_process(FIXTURE_DROPOUT)
    ro  = next((r for r in pkg.reasoning if r.anomaly_type == "DROPOUT"), None)
    assert ro is not None, "DROPOUT not found in reasoning output"
    assert ro.severity == "critical"
    assert pkg.is_blocked
    assert "2 hour" in ro.urgency.lower(), f"Urgency text unexpected: {ro.urgency}"
    keywords = {"disconnect", "shutdown", "communication", "fuse", "relay", "grid"}
    actions_text = " ".join(ro.recommended_actions).lower()
    assert any(k in actions_text for k in keywords), (
        f"No disconnect-related keyword in actions: {ro.recommended_actions}"
    )


def test_sensor_fault_classification():
    """
    Synthetic: −20°C module temp at 500 W/m² — thermodynamically impossible.
    Expected: SENSOR_FAULT detected, actions mention calibration/replacement.
    """
    pkg = _analyse_and_process(FIXTURE_SENSOR)
    ro  = next((r for r in pkg.reasoning if r.anomaly_type == "SENSOR_FAULT"), None)
    assert ro is not None, "SENSOR_FAULT not found in reasoning output"
    keywords = {"sensor", "calibrat", "replac", "wiring", "verify"}
    actions_text = " ".join(ro.recommended_actions).lower()
    assert any(k in actions_text for k in keywords), (
        f"No sensor-related keyword in actions: {ro.recommended_actions}"
    )


# ══════════════════════════════════════════════════════════════════════════
# GROUP 4 — Severity → blocked mapping
# ══════════════════════════════════════════════════════════════════════════

def test_critical_violation_blocks_data():
    """Any CRITICAL violation in the result must set is_blocked=True."""
    for fixture in (FIXTURE_OVERP, FIXTURE_THERMAL, FIXTURE_DROPOUT):
        result = analyse(fixture)
        if result.is_clean:
            continue
        if any(v.severity == Severity.CRITICAL for v in result.violations):
            pkg = _engine().process(result)
            assert pkg.is_blocked, (
                f"{fixture.inverter_id}: CRITICAL present but is_blocked=False"
            )


def test_warning_only_does_not_block():
    """WARNING-only alerts must NOT block the data point."""
    result = analyse(FIXTURE_MPPT)
    assert not result.is_clean
    # Verify all violations are WARNING, not CRITICAL
    assert all(v.severity == Severity.WARNING for v in result.violations), (
        "Expected only WARNING violations for MPPT_FAILURE fixture"
    )
    pkg = _engine().process(result)
    assert not pkg.is_blocked, "WARNING-only alert must not block data"


def test_clean_data_action_text_matches_severity():
    """
    CRITICAL → text must contain 'BLOCKED'.
    WARNING  → text must contain 'FLAGGED'.
    """
    for fixture, expected_keyword in [
        (FIXTURE_OVERP, "BLOCKED"),
        (FIXTURE_MPPT,  "FLAGGED"),
    ]:
        result = analyse(fixture)
        if result.is_clean:
            continue
        pkg = _engine().process(result)
        for ro in pkg.reasoning:
            assert expected_keyword in ro.clean_data_action, (
                f"{ro.anomaly_type}: expected '{expected_keyword}' in clean_data_action, "
                f"got: {ro.clean_data_action!r}"
            )


# ══════════════════════════════════════════════════════════════════════════
# GROUP 5 — Graceful degradation
# ══════════════════════════════════════════════════════════════════════════

def test_reasoning_source_is_rule_based():
    """Without AI key, reasoning_source must always be 'rule_based'."""
    engine = ReasoningEngine(use_ai=False)
    for fixture in (FIXTURE_MPPT, FIXTURE_OVERP, FIXTURE_THERMAL):
        result = analyse(fixture)
        if result.is_clean:
            continue
        pkg = engine.process(result)
        for ro in pkg.reasoning:
            assert ro.reasoning_source == "rule_based", (
                f"Expected 'rule_based', got '{ro.reasoning_source}'"
            )


def test_multiple_violations_in_one_package():
    """
    A DataPoint can trigger multiple rules simultaneously.
    All violations must appear in the reasoning output.
    """
    # Sensor fault + MPPT failure: cold temp + high irr + zero power
    dp = _dp(power=0.0, irr=600.0, temp=-20.0, volt=320.0, curr=0.0)
    result = analyse(dp)
    assert len(result.violations) >= 2, (
        f"Expected ≥2 violations, got {len(result.violations)}: "
        f"{[v.anomaly_type.value for v in result.violations]}"
    )
    pkg = _engine().process(result)
    assert len(pkg.reasoning) == len(result.violations), (
        f"reasoning count ({len(pkg.reasoning)}) ≠ violations ({len(result.violations)})"
    )
    assert len(pkg.raw_violations) == len(result.violations)


def test_unknown_anomaly_type_has_fallback_action():
    """
    If an anomaly type has no entry in FALLBACK_ACTIONS, the engine must
    still return at least one recommended action (the generic fallback).
    """
    engine = ReasoningEngine(use_ai=False)
    # Manufacture a violation with a type that has no FALLBACK_ACTIONS entry
    violation = PhysicsViolation(
        anomaly_type  = AnomalyType.ENERGY_BALANCE,   # not in FALLBACK_ACTIONS
        severity      = Severity.WARNING,
        message       = "Synthetic energy balance violation for testing",
        rule_id       = "TEST-001",
        measured      = 50.0,
        expected_min  = 0.0,
        expected_max  = 10.0,
        confidence    = 0.75,
    )
    ro = engine._build_rule_based_output(violation)
    assert len(ro.recommended_actions) >= 1, "No fallback action returned for unknown anomaly type"
    assert isinstance(ro.recommended_actions[0], str) and len(ro.recommended_actions[0]) > 0


# ══════════════════════════════════════════════════════════════════════════
# GROUP 6 — Batch / real-data sampling test
# ══════════════════════════════════════════════════════════════════════════

def test_real_data_batch_processing():
    """
    Load real rows from plant_2, skip nighttime (irr<50 W/m²), process up to
    500 daytime rows, and assert:
    - No uncaught exceptions
    - At least one anomaly is detected (first STRING_SHADING appears at row ~244)
    - Every alert has ≥1 reasoning entry with valid fields
    - Confidence scores are within [0.50, 0.99]
    - reasoning_source is always 'rule_based'
    """
    import csv
    from datetime import datetime
    from pathlib import Path

    archive = Path.home() / "Desktop" / "archive"
    engine  = _engine()

    def parse_dt(raw):
        raw = raw.strip()
        for fmt in ("%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try: return datetime.strptime(raw, fmt)
            except ValueError: continue
        return None

    # Build weather index
    weather: dict = {}
    with open(archive / "Plant_2_Weather_Sensor_Data.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dt = parse_dt(row["DATE_TIME"])
            if dt:
                weather[dt] = float(row.get("IRRADIATION", 0) or 0) * 1000

    errors   = []
    alerts   = []
    seen     = 0          # daytime rows processed
    prev_map = {}

    with open(archive / "Plant_2_Generation_Data.csv", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if seen >= 500:
                break
            dt  = parse_dt(row["DATE_TIME"])
            irr = weather.get(dt, 0.0) if dt else 0.0

            # Skip nighttime rows — irr=0 triggers no physics rules
            if irr < 50:
                continue
            seen += 1

            ac  = float(row.get("AC_POWER", 0) or 0)
            dc  = float(row.get("DC_POWER", 0) or 0)
            inv = row["SOURCE_KEY"]
            dp  = DataPoint(
                timestamp      = dt.isoformat() if dt else "unknown",
                inverter_id    = f"plant_2::{inv}",
                power_kw       = ac if ac != 0 else dc,
                irradiance_wm2 = irr,
                voltage_v      = 320.0,
                current_a      = dc * 1000 / 320.0 if dc > 0 else 0.0,
                temperature_c  = 45.0,
                capacity_kw    = 1522.8,
                voc            = 400.0,
            )
            result = analyse(dp, prev_map.get(inv))
            prev_map[inv] = dp

            if not result.is_clean:
                try:
                    pkg = engine.process(result)
                    alerts.append(pkg)
                    for ro in pkg.reasoning:
                        if not (0.50 <= ro.confidence_score <= 0.99):
                            errors.append(
                                f"Confidence {ro.confidence_score} out of range "
                                f"for {ro.anomaly_type} on {inv}"
                            )
                        if ro.reasoning_source != "rule_based":
                            errors.append(f"Unexpected source: {ro.reasoning_source}")
                        if not ro.recommended_actions:
                            errors.append(f"Empty actions for {ro.anomaly_type} on {inv}")
                except Exception as e:
                    errors.append(f"Exception processing {inv}: {e}")

    assert not errors, "Errors during batch processing:\n" + "\n".join(errors)
    assert len(alerts) > 0, (
        f"No anomalies found in {seen} daytime rows — "
        "first STRING_SHADING expected around row 244."
    )
    for pkg in alerts:
        assert pkg.reasoning, f"Alert {pkg.alert_id} has empty reasoning list"


# ══════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════

TESTS = [
    # Group 1 — Output contract
    ("Output contract / AlertPackage fields",          test_alert_package_fields),
    ("Output contract / ReasoningOutput fields",       test_reasoning_output_fields),
    ("Output contract / raw_violations fields",        test_raw_violations_fields),
    ("Output contract / unique alert_id per call",     test_alert_id_unique_per_call),
    # Group 2 — Confidence calibration
    ("Confidence / all scores within [0.50, 0.99]",   test_confidence_within_bounds),
    ("Confidence / blending formula correct",          test_confidence_blending_formula),
    ("Confidence / false positive lowers baseline",    test_false_positive_feedback_lowers_confidence),
    ("Confidence / confirmed raises baseline",         test_confirmed_feedback_raises_confidence),
    ("Confidence / negative delta clamped at -0.20",  test_feedback_cumulative_delta_clamped),
    ("Confidence / positive delta clamped at +0.10",  test_feedback_positive_delta_clamped),
    # Group 3 — Classification quality
    ("Classification / MPPT_FAILURE (real data)",     test_mppt_failure_classification),
    ("Classification / OVER_POWER (real data)",       test_over_power_classification),
    ("Classification / STRING_SHADING (real data)",   test_string_shading_classification),
    ("Classification / THERMAL_LIMIT (synthetic)",    test_thermal_limit_classification),
    ("Classification / DROPOUT (synthetic)",          test_dropout_classification),
    ("Classification / SENSOR_FAULT (synthetic)",     test_sensor_fault_classification),
    # Group 4 — Severity → blocked mapping
    ("Severity / CRITICAL always blocks",             test_critical_violation_blocks_data),
    ("Severity / WARNING-only does not block",        test_warning_only_does_not_block),
    ("Severity / clean_data_action text matches",     test_clean_data_action_text_matches_severity),
    # Group 5 — Graceful degradation
    ("Degradation / reasoning_source=rule_based",     test_reasoning_source_is_rule_based),
    ("Degradation / multi-violation package",         test_multiple_violations_in_one_package),
    ("Degradation / unknown type gets fallback",      test_unknown_anomaly_type_has_fallback_action),
    # Group 6 — Real-data batch
    ("Real-data / 200-row batch (plant_2)",           test_real_data_batch_processing),
]


def main():
    print(f"\n{BOLD}{CYAN}Guardian P — Reasoning Engine Test Suite{RESET}")
    print(f"{CYAN}{'─' * 55}{RESET}")

    groups = {}
    for name, fn in TESTS:
        group = name.split("/")[0].strip()
        groups.setdefault(group, []).append((name, fn))

    for group, tests in groups.items():
        print(f"\n{BOLD}  {group}{RESET}")
        for name, fn in tests:
            label = name.split("/", 1)[-1].strip()
            run_test(label, fn)

    # ── Summary ────────────────────────────────────────────────────────
    total   = len(results)
    passed  = sum(1 for r in results if r["status"] == "PASS")
    failed  = sum(1 for r in results if r["status"] == "FAIL")
    errors  = sum(1 for r in results if r["status"] == "ERROR")
    avg_ms  = sum(r["ms"] for r in results) / total if total else 0

    print(f"\n{CYAN}{'─' * 55}{RESET}")
    print(f"{BOLD}  Results: {GREEN}{passed} passed{RESET}", end="")
    if failed: print(f"  {RED}{failed} failed{RESET}", end="")
    if errors: print(f"  {RED}{errors} errors{RESET}", end="")
    print(f"  {YELLOW}/ {total} total{RESET}  (avg {avg_ms:.0f}ms/test)")

    if failed or errors:
        print(f"\n{BOLD}{RED}  Failed tests:{RESET}")
        for r in results:
            if r["status"] in ("FAIL", "ERROR"):
                print(f"  {RED}✗{RESET}  {r['name']}")
                print(f"      {r.get('error','')}")

    # Machine-readable summary for CI
    print(f"\n  Exit code: {'0' if not (failed or errors) else '1'}")
    return 0 if not (failed or errors) else 1


if __name__ == "__main__":
    sys.exit(main())
