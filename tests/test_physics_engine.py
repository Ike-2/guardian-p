"""
Guardian P — Test Suite
========================
Validates the physics engine against known anomaly scenarios.
Run with: python -m pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.physics_engine import DataPoint, analyse, AnomalyType, Severity


def make_normal(power=72.5, irr=750, volt=385, curr=188, temp=42, inv="INV-001"):
    return DataPoint(
        timestamp      = "2025-06-01T10:30:00Z",
        inverter_id    = inv,
        power_kw       = power,
        irradiance_wm2 = irr,
        voltage_v      = volt,
        current_a      = curr,
        temperature_c  = temp,
        capacity_kw    = 100.0,
        voc            = 400.0,
        isc            = 10.0,
    )


# ── CLEAN DATA ─────────────────────────────────────────────────────────────

def test_clean_normal_operation():
    """Normal sunny day operation should produce zero violations."""
    dp = make_normal()
    result = analyse(dp)
    assert result.is_clean, f"Expected clean, got: {result.violations}"


def test_clean_nighttime():
    """Nighttime zero-power should not flag anything."""
    dp = make_normal(power=0, irr=0, volt=0, curr=0, temp=18)
    result = analyse(dp)
    assert result.is_clean


def test_clean_cloudy():
    """Reduced output on overcast day is physically valid."""
    dp = make_normal(power=8.0, irr=100, volt=360, curr=22, temp=25)
    result = analyse(dp)
    assert result.is_clean


# ── PHYSICS VIOLATIONS ─────────────────────────────────────────────────────

def test_over_power_detected():
    """Output exceeding physical maximum must be flagged as CRITICAL."""
    dp = make_normal(power=200.0, irr=750)  # 200 kW from 100 kW system
    result = analyse(dp)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.OVER_POWER in types
    assert result.worst_severity == Severity.CRITICAL


def test_mppt_failure_detected():
    """Very low output during high irradiance must be flagged."""
    dp = make_normal(power=1.0, irr=800)  # 1 kW from 100 kW system at 800 W/m²
    result = analyse(dp)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.MPPT_FAILURE in types


def test_overvoltage_detected():
    """Voltage above Voc × 1.05 must be flagged as CRITICAL."""
    dp = make_normal(volt=430)   # Voc=400, limit=420
    result = analyse(dp)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.OVERVOLTAGE in types
    assert result.worst_severity == Severity.CRITICAL


def test_thermal_limit_detected():
    """Temperature > 85°C must be flagged as CRITICAL."""
    dp = make_normal(temp=92)
    result = analyse(dp)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.THERMAL_LIMIT in types
    assert result.worst_severity == Severity.CRITICAL


def test_sensor_fault_cold_plus_high_irr():
    """Sub-zero temp with high irradiance is physically impossible."""
    dp = make_normal(temp=-20, irr=600)
    result = analyse(dp)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.SENSOR_FAULT in types


def test_daytime_dropout_detected():
    """Zero output at high irradiance must be flagged as CRITICAL."""
    dp = make_normal(power=0, irr=700, volt=0, curr=0)
    result = analyse(dp)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.DROPOUT in types
    assert result.worst_severity == Severity.CRITICAL


def test_string_shading_detected():
    """Sudden current drop at stable irradiance must trigger STRING_SHADING."""
    prev = make_normal(curr=188, irr=750)
    curr = make_normal(curr=80, irr=740)   # 57% drop, irradiance stable
    result = analyse(curr, prev)
    assert not result.is_clean
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.STRING_SHADING in types


def test_string_shading_not_false_positive_on_irr_change():
    """Current drop caused by irradiance drop should NOT trigger STRING_SHADING."""
    prev = make_normal(curr=188, irr=750)
    curr_dp = make_normal(curr=80, irr=310)   # irradiance dropped 58% — current drop is expected
    result = analyse(curr_dp, prev)
    types = [v.anomaly_type for v in result.violations]
    assert AnomalyType.STRING_SHADING not in types


# ── SELF-LEARNING ──────────────────────────────────────────────────────────

def test_false_positive_reduces_confidence():
    from core.reasoning_engine import ReasoningEngine
    engine = ReasoningEngine(use_ai=False)
    initial = engine.get_learning_state()[AnomalyType.MPPT_FAILURE.value]["effective"]
    engine.apply_feedback(AnomalyType.MPPT_FAILURE, is_false_positive=True)
    updated = engine.get_learning_state()[AnomalyType.MPPT_FAILURE.value]["effective"]
    assert updated < initial, "False positive should reduce effective confidence"


def test_confirmed_increases_confidence():
    from core.reasoning_engine import ReasoningEngine
    engine = ReasoningEngine(use_ai=False)
    initial = engine.get_learning_state()[AnomalyType.THERMAL_LIMIT.value]["effective"]
    engine.apply_feedback(AnomalyType.THERMAL_LIMIT, is_false_positive=False)
    updated = engine.get_learning_state()[AnomalyType.THERMAL_LIMIT.value]["effective"]
    assert updated > initial, "Confirmed alert should increase effective confidence"


if __name__ == "__main__":
    tests = [
        test_clean_normal_operation,
        test_clean_nighttime,
        test_clean_cloudy,
        test_over_power_detected,
        test_mppt_failure_detected,
        test_overvoltage_detected,
        test_thermal_limit_detected,
        test_sensor_fault_cold_plus_high_irr,
        test_daytime_dropout_detected,
        test_string_shading_detected,
        test_string_shading_not_false_positive_on_irr_change,
        test_false_positive_reduces_confidence,
        test_confirmed_increases_confidence,
    ]

    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗  {t.__name__}  →  {e}")

    print(f"\n{passed}/{len(tests)} tests passed")
