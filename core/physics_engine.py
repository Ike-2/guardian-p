"""
Guardian P — Physics Constraint Engine
=======================================
Priority 1: Physical law validation for solar PV data streams.
Detects anomalies that are statistically plausible but physically impossible.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING  = "warning"
    INFO     = "info"


class AnomalyType(str, Enum):
    OVER_POWER      = "OVER_POWER"
    MPPT_FAILURE    = "MPPT_FAILURE"
    STRING_SHADING  = "STRING_SHADING"
    OVERVOLTAGE     = "OVERVOLTAGE"
    UNDERVOLTAGE    = "UNDERVOLTAGE"
    THERMAL_LIMIT   = "THERMAL_LIMIT"
    SENSOR_FAULT    = "SENSOR_FAULT"
    SENSOR_DRIFT    = "SENSOR_DRIFT"
    DROPOUT         = "DROPOUT"
    ENERGY_BALANCE  = "ENERGY_BALANCE"


@dataclass
class PhysicsViolation:
    anomaly_type:  AnomalyType
    severity:      Severity
    message:       str
    rule_id:       str
    measured:      float
    expected_min:  float
    expected_max:  float
    confidence:    float          # 0–1, based on how far the value deviates


@dataclass
class DataPoint:
    """Normalised input schema for one inverter reading."""
    timestamp:       str
    inverter_id:     str
    power_kw:        float
    irradiance_wm2:  float
    voltage_v:       float
    current_a:       float
    temperature_c:   float
    capacity_kw:     float = 100.0   # nameplate capacity
    voc:             float = 400.0   # open-circuit voltage at STC
    isc:             float = 10.0    # short-circuit current at STC


@dataclass
class AnalysisResult:
    data_point:  DataPoint
    violations:  list[PhysicsViolation] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0

    @property
    def worst_severity(self) -> Optional[Severity]:
        if not self.violations:
            return None
        order = [Severity.CRITICAL, Severity.WARNING, Severity.INFO]
        for s in order:
            if any(v.severity == s for v in self.violations):
                return s
        return None


# ── CONFIDENCE HELPERS ─────────────────────────────────────────────────────

def _deviation_confidence(measured: float, expected: float, scale: float) -> float:
    """Confidence rises with deviation magnitude. Capped at 0.99."""
    if scale == 0:
        return 0.99
    ratio = abs(measured - expected) / scale
    return round(min(0.99, 0.70 + 0.29 * math.tanh(ratio - 1)), 3)


# ── RULE IMPLEMENTATIONS ───────────────────────────────────────────────────

def rule_power_vs_irradiance(dp: DataPoint) -> Optional[PhysicsViolation]:
    """
    Rule PV-001: Output power must be physically consistent with irradiance.
    At irradiance G (W/m²), max possible output = capacity × (G/1000) × 1.08
    (1.08 accounts for cold-temperature bonus and measurement tolerance).
    Min expected (if no severe fault) = capacity × (G/1000) × 0.05
    """
    if dp.irradiance_wm2 < 50:
        return None  # nighttime / near-dark — rule not applicable

    theoretical_max = dp.capacity_kw * (dp.irradiance_wm2 / 1000) * 1.08
    theoretical_min = dp.capacity_kw * (dp.irradiance_wm2 / 1000) * 0.05

    if dp.power_kw > theoretical_max:
        conf = _deviation_confidence(dp.power_kw, theoretical_max, dp.capacity_kw * 0.1)
        return PhysicsViolation(
            anomaly_type  = AnomalyType.OVER_POWER,
            severity      = Severity.CRITICAL,
            rule_id       = "PV-001-HIGH",
            message       = (
                f"Reported power {dp.power_kw} kW exceeds physical maximum "
                f"{theoretical_max:.1f} kW at {dp.irradiance_wm2} W/m² irradiance. "
                "Likely cause: meter fault, CT clamp error, or data transmission corruption."
            ),
            measured      = dp.power_kw,
            expected_min  = theoretical_min,
            expected_max  = theoretical_max,
            confidence    = conf,
        )

    if dp.power_kw < theoretical_min and dp.irradiance_wm2 > 200:
        conf = _deviation_confidence(theoretical_min, dp.power_kw, dp.capacity_kw * 0.05)
        return PhysicsViolation(
            anomaly_type  = AnomalyType.MPPT_FAILURE,
            severity      = Severity.WARNING,
            rule_id       = "PV-001-LOW",
            message       = (
                f"Output {dp.power_kw} kW is far below minimum expected "
                f"{theoretical_min:.1f} kW at {dp.irradiance_wm2} W/m². "
                "Possible MPPT tracking failure, partial string disconnect, or heavy soiling."
            ),
            measured      = dp.power_kw,
            expected_min  = theoretical_min,
            expected_max  = theoretical_max,
            confidence    = conf,
        )

    return None


def rule_voltage_bounds(dp: DataPoint) -> Optional[PhysicsViolation]:
    """
    Rule PV-002: DC bus voltage must stay within IV-curve physical limits.
    Voltage > Voc × 1.05 → physically impossible (exceeds open-circuit limit).
    Voltage < Voc × 0.25 during daylight → sensor drift or disconnect.
    """
    upper_limit = dp.voc * 1.05
    lower_limit = dp.voc * 0.25

    if dp.voltage_v > upper_limit:
        conf = _deviation_confidence(dp.voltage_v, upper_limit, dp.voc * 0.05)
        return PhysicsViolation(
            anomaly_type  = AnomalyType.OVERVOLTAGE,
            severity      = Severity.CRITICAL,
            rule_id       = "PV-002-HIGH",
            message       = (
                f"Voltage {dp.voltage_v} V exceeds open-circuit limit "
                f"{upper_limit:.1f} V. Physically impossible under normal IV-curve behaviour. "
                "Immediate inspection recommended — risk of insulation failure."
            ),
            measured      = dp.voltage_v,
            expected_min  = lower_limit,
            expected_max  = upper_limit,
            confidence    = conf,
        )

    if dp.voltage_v < lower_limit and dp.irradiance_wm2 > 150:
        conf = _deviation_confidence(lower_limit, dp.voltage_v, dp.voc * 0.1)
        return PhysicsViolation(
            anomaly_type  = AnomalyType.SENSOR_DRIFT,
            severity      = Severity.INFO,
            rule_id       = "PV-002-LOW",
            message       = (
                f"Voltage {dp.voltage_v} V is below expected operating range "
                f"{lower_limit:.1f}–{upper_limit:.1f} V during daylight conditions. "
                "Possible sensor calibration drift or partial string disconnection."
            ),
            measured      = dp.voltage_v,
            expected_min  = lower_limit,
            expected_max  = upper_limit,
            confidence    = conf,
        )

    return None


def rule_current_drop(dp: DataPoint, prev_dp: Optional[DataPoint]) -> Optional[PhysicsViolation]:
    """
    Rule PV-003: Sudden current drop > 35% within one sampling interval
    at stable irradiance → string-level shading or bypass diode event.
    """
    if prev_dp is None or dp.irradiance_wm2 < 100:
        return None

    irr_change = abs(dp.irradiance_wm2 - prev_dp.irradiance_wm2) / max(1, prev_dp.irradiance_wm2)
    if irr_change > 0.20:
        return None  # irradiance itself changed; current drop is expected

    if prev_dp.current_a <= 0:
        return None

    drop_pct = (prev_dp.current_a - dp.current_a) / prev_dp.current_a * 100

    if drop_pct > 35:
        conf = _deviation_confidence(drop_pct, 35, 20)
        return PhysicsViolation(
            anomaly_type  = AnomalyType.STRING_SHADING,
            severity      = Severity.WARNING,
            rule_id       = "PV-003",
            message       = (
                f"Current dropped {drop_pct:.1f}% ({prev_dp.current_a:.1f} → {dp.current_a:.1f} A) "
                f"while irradiance remained stable ({dp.irradiance_wm2} W/m²). "
                "Consistent with string-level shading, bypass diode activation, or partial connector failure."
            ),
            measured      = dp.current_a,
            expected_min  = prev_dp.current_a * 0.65,
            expected_max  = prev_dp.current_a * 1.10,
            confidence    = conf,
        )

    return None


def rule_thermal_limits(dp: DataPoint) -> Optional[PhysicsViolation]:
    """
    Rule PV-004: Module temperature physical bounds.
    > 85°C → exceeds IEC 61215 design limit, deration active.
    < -15°C with high irradiance → thermodynamically inconsistent.
    """
    if dp.temperature_c > 85:
        conf = _deviation_confidence(dp.temperature_c, 85, 10)
        return PhysicsViolation(
            anomaly_type  = AnomalyType.THERMAL_LIMIT,
            severity      = Severity.CRITICAL,
            rule_id       = "PV-004-HIGH",
            message       = (
                f"Module temperature {dp.temperature_c}°C exceeds IEC 61215 design limit (85°C). "
                "Output deration is active. Inspect cooling, ventilation, and soiling level."
            ),
            measured      = dp.temperature_c,
            expected_min  = -20.0,
            expected_max  = 85.0,
            confidence    = conf,
        )

    if dp.temperature_c < -15 and dp.irradiance_wm2 > 300:
        return PhysicsViolation(
            anomaly_type  = AnomalyType.SENSOR_FAULT,
            severity      = Severity.INFO,
            rule_id       = "PV-004-LOW",
            message       = (
                f"Temperature {dp.temperature_c}°C is physically inconsistent with "
                f"irradiance {dp.irradiance_wm2} W/m². A surface exposed to 300+ W/m² "
                "cannot remain below -15°C. Likely temperature sensor fault."
            ),
            measured      = dp.temperature_c,
            expected_min  = -15.0,
            expected_max  = 85.0,
            confidence    = 0.92,
        )

    return None


def rule_daytime_dropout(dp: DataPoint) -> Optional[PhysicsViolation]:
    """
    Rule PV-005: Zero output during significant daylight is a critical event.
    """
    if dp.irradiance_wm2 > 200 and dp.power_kw == 0 and dp.voltage_v < 5:
        return PhysicsViolation(
            anomaly_type  = AnomalyType.DROPOUT,
            severity      = Severity.CRITICAL,
            rule_id       = "PV-005",
            message       = (
                f"Complete output dropout at {dp.irradiance_wm2} W/m² irradiance. "
                "Inverter may have triggered emergency shutdown, communication link lost, "
                "or AC/DC disconnect opened. Immediate field check required."
            ),
            measured      = dp.power_kw,
            expected_min  = dp.capacity_kw * (dp.irradiance_wm2 / 1000) * 0.05,
            expected_max  = dp.capacity_kw * (dp.irradiance_wm2 / 1000) * 1.08,
            confidence    = 0.99,
        )
    return None


# ── ENGINE ENTRY POINT ─────────────────────────────────────────────────────

ALL_RULES = [
    rule_power_vs_irradiance,
    rule_voltage_bounds,
    rule_thermal_limits,
    rule_daytime_dropout,
]


def analyse(dp: DataPoint, prev_dp: Optional[DataPoint] = None) -> AnalysisResult:
    """
    Run all physics rules against a single data point.
    Returns an AnalysisResult with zero or more PhysicsViolation objects.

    Input sanitisation
    ------------------
    Before running any rule we clamp or reject values that are physically
    impossible and would otherwise produce misleading rule outputs:

    • irradiance_wm2 < 0  → treated as 0 (sensor offset artefact)
    • power_kw < 0        → flagged as SENSOR_FAULT and clamped to 0
                            (negative AC power is physically impossible)
    • capacity_kw ≤ 0     → rule PV-001 would produce theoretical_max=0
                            and flag everything as OVER_POWER; guard to 1 kW
    • voc ≤ 0             → rule PV-002 divides by voc; guard to 1 V
    • voltage_v < 0       → clamped to 0 (sensor polarity flip)
    • current_a < 0       → clamped to 0 (CT clamp polarity artefact)
    """
    violations: list[PhysicsViolation] = []

    # ── Clamp / guard unphysical inputs ────────────────────────────────────
    import dataclasses
    cleaned = dataclasses.replace(
        dp,
        irradiance_wm2 = max(0.0, dp.irradiance_wm2),
        voltage_v      = max(0.0, dp.voltage_v),
        current_a      = max(0.0, dp.current_a),
        capacity_kw    = max(1.0, dp.capacity_kw),
        voc            = max(1.0, dp.voc),
    )

    # Negative power is physically impossible → SENSOR_FAULT flag
    if dp.power_kw < 0:
        violations.append(PhysicsViolation(
            anomaly_type  = AnomalyType.SENSOR_FAULT,
            severity      = Severity.WARNING,
            rule_id       = "PV-000-NEG",
            message       = (
                f"Reported power {dp.power_kw} kW is negative, which is physically "
                "impossible for a PV inverter. Likely CT clamp polarity reversal "
                "or data logger sign convention error."
            ),
            measured      = dp.power_kw,
            expected_min  = 0.0,
            expected_max  = dp.capacity_kw,
            confidence    = 0.95,
        ))
        cleaned = dataclasses.replace(cleaned, power_kw=0.0)

    # Similarly clamp prev_dp if provided
    if prev_dp is not None:
        prev_dp = dataclasses.replace(
            prev_dp,
            irradiance_wm2 = max(0.0, prev_dp.irradiance_wm2),
            voltage_v      = max(0.0, prev_dp.voltage_v),
            current_a      = max(0.0, prev_dp.current_a),
            capacity_kw    = max(1.0, prev_dp.capacity_kw),
            voc            = max(1.0, prev_dp.voc),
            power_kw       = max(0.0, prev_dp.power_kw),
        )

    # ── Run stateless rules ─────────────────────────────────────────────────
    for rule in ALL_RULES:
        v = rule(cleaned)
        if v:
            violations.append(v)

    # ── Stateful rule (needs previous point) ───────────────────────────────
    v_drop = rule_current_drop(cleaned, prev_dp)
    if v_drop:
        violations.append(v_drop)

    # Return result with the *original* data_point for full auditability,
    # but violations were computed on the sanitised copy.
    return AnalysisResult(data_point=dp, violations=violations)
