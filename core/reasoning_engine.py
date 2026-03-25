"""
Guardian P — AI Reasoning Layer
================================
Priority 2: Takes PhysicsViolation objects and produces structured,
explainable, actionable intelligence for operators.

This layer wraps the Anthropic Claude API to generate natural-language
reasoning. Falls back to rule-based templates when API is unavailable,
ensuring the system always produces output.
"""

from dataclasses import dataclass, field
from typing import Optional
import json
import logging
import os

from core.physics_engine import AnalysisResult, PhysicsViolation, AnomalyType, Severity

logger = logging.getLogger(__name__)


# ── CONFIDENCE SCORE BASELINES ─────────────────────────────────────────────
# Baseline detection confidence per anomaly type (tuned by feedback over time)
CONFIDENCE_BASELINES: dict[AnomalyType, float] = {
    AnomalyType.OVER_POWER:     0.94,
    AnomalyType.MPPT_FAILURE:   0.87,
    AnomalyType.STRING_SHADING: 0.81,
    AnomalyType.OVERVOLTAGE:    0.97,
    AnomalyType.UNDERVOLTAGE:   0.78,
    AnomalyType.THERMAL_LIMIT:  0.93,
    AnomalyType.SENSOR_FAULT:   0.85,
    AnomalyType.SENSOR_DRIFT:   0.76,
    AnomalyType.DROPOUT:        0.99,
    AnomalyType.ENERGY_BALANCE: 0.82,
}

# Urgency levels → estimated response window
URGENCY_MAP = {
    Severity.CRITICAL: "Respond within 2 hours",
    Severity.WARNING:  "Respond within 24 hours",
    Severity.INFO:     "Log and review at next maintenance cycle",
}

# Rule-based fallback recommendations (used when Claude API is unavailable)
FALLBACK_ACTIONS: dict[AnomalyType, list[str]] = {
    AnomalyType.OVER_POWER: [
        "Verify meter CT ratio and wiring polarity",
        "Check data logger firmware for scaling errors",
        "Cross-reference with adjacent inverter output",
    ],
    AnomalyType.MPPT_FAILURE: [
        "Check inverter MPPT algorithm settings and firmware version",
        "Inspect DC string combiner box for loose connections",
        "Measure open-circuit voltage at inverter DC input terminals",
        "Check for heavy soiling on affected string",
    ],
    AnomalyType.STRING_SHADING: [
        "Identify shading source (new obstruction, soiling, bird droppings)",
        "Inspect bypass diodes in junction boxes",
        "Review string-level production data for persistent pattern",
    ],
    AnomalyType.OVERVOLTAGE: [
        "IMMEDIATE: Do not perform live work until voltage is confirmed safe",
        "Verify DC disconnect switch operation",
        "Check surge protection device (SPD) condition",
        "Inspect for ground fault conditions",
    ],
    AnomalyType.THERMAL_LIMIT: [
        "Inspect module surface for soiling, hotspots, or delamination",
        "Verify ambient temperature sensor accuracy",
        "Check ventilation clearances around inverter",
        "Review performance ratio trend for gradual degradation",
    ],
    AnomalyType.SENSOR_FAULT: [
        "Replace or recalibrate the faulty sensor",
        "Verify sensor wiring and connector integrity",
        "Compare readings with nearby weather station data",
    ],
    AnomalyType.SENSOR_DRIFT: [
        "Schedule sensor recalibration at next maintenance visit",
        "Cross-check with reference sensor if available",
        "Flag data from this period as low-confidence in historical records",
    ],
    AnomalyType.DROPOUT: [
        "URGENT: Check inverter AC and DC disconnect status",
        "Verify communication link (Modbus/RS485) to inverter",
        "Check grid connection and protection relay status",
        "Inspect fuses in DC combiner box",
    ],
}


# ── RESPONSE SCHEMA ────────────────────────────────────────────────────────

@dataclass
class ReasoningOutput:
    anomaly_type:      str
    severity:          str
    confidence_score:  float          # final score after self-learning adjustment
    root_cause:        str            # primary hypothesis
    supporting_facts:  list[str]      # data points that support the diagnosis
    recommended_actions: list[str]    # ordered action list
    urgency:           str
    clean_data_action: str            # what Guardian P does with this data point
    reasoning_source:  str            # "ai_api" | "rule_based"


@dataclass
class AlertPackage:
    """Full output package sent to downstream systems / operator dashboard."""
    alert_id:     str
    timestamp:    str
    inverter_id:  str
    is_blocked:   bool              # True = data point withheld from downstream AI
    reasoning:    list[ReasoningOutput]
    raw_violations: list[dict]      # serialised PhysicsViolation objects


# ── REASONING ENGINE ───────────────────────────────────────────────────────

class ReasoningEngine:
    """
    Wraps Claude API for explainable anomaly reasoning.
    Falls back to deterministic templates if API key is not set.
    """

    def __init__(self, api_key: Optional[str] = None, use_ai: bool = True):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.use_ai  = use_ai and bool(self.api_key)
        self._learned_adjustments: dict[AnomalyType, float] = {}  # from feedback loop

    def get_confidence(self, anomaly_type: AnomalyType, physics_conf: float) -> float:
        """
        Blend physics-engine confidence with learned adjustment from feedback.
        Learned adjustments shift baseline by ±0.05 per confirmed/false-positive signal.
        """
        baseline = CONFIDENCE_BASELINES.get(anomaly_type, 0.80)
        adjustment = self._learned_adjustments.get(anomaly_type, 0.0)
        blended = (physics_conf * 0.6) + (baseline * 0.4) + adjustment
        return round(max(0.50, min(0.99, blended)), 3)

    def apply_feedback(self, anomaly_type: AnomalyType, is_false_positive: bool):
        """
        Self-learning: update confidence baseline based on operator feedback.
        False positive → reduce confidence. Confirmed → small increase.
        """
        delta = -0.04 if is_false_positive else +0.01
        current = self._learned_adjustments.get(anomaly_type, 0.0)
        self._learned_adjustments[anomaly_type] = round(
            max(-0.20, min(0.10, current + delta)), 3
        )

    def get_learning_state(self) -> dict:
        """Return current learned adjustments for all anomaly types."""
        return {
            atype.value: {
                "baseline": CONFIDENCE_BASELINES.get(atype, 0.80),
                "adjustment": self._learned_adjustments.get(atype, 0.0),
                "effective": round(
                    CONFIDENCE_BASELINES.get(atype, 0.80) +
                    self._learned_adjustments.get(atype, 0.0), 3
                ),
            }
            for atype in AnomalyType
        }

    def _build_rule_based_output(self, violation: PhysicsViolation) -> ReasoningOutput:
        """Deterministic fallback reasoning (no API required)."""
        actions = FALLBACK_ACTIONS.get(violation.anomaly_type, ["Inspect the affected inverter manually."])
        confidence = self.get_confidence(violation.anomaly_type, violation.confidence)
        clean_action = (
            "DATA BLOCKED — this reading has been withheld from downstream AI systems."
            if violation.severity == Severity.CRITICAL
            else "DATA FLAGGED — forwarded with anomaly tag; downstream AI should treat with reduced weight."
        )
        return ReasoningOutput(
            anomaly_type       = violation.anomaly_type.value,
            severity           = violation.severity.value,
            confidence_score   = confidence,
            root_cause         = violation.message,
            supporting_facts   = [
                f"Measured value: {violation.measured}",
                f"Expected range: {violation.expected_min:.2f} – {violation.expected_max:.2f}",
                f"Rule triggered: {violation.rule_id}",
            ],
            recommended_actions = actions,
            urgency             = URGENCY_MAP.get(violation.severity, "Review at next opportunity"),
            clean_data_action   = clean_action,
            reasoning_source    = "rule_based",
        )

    def _build_ai_output(self, violation: PhysicsViolation, context: dict) -> ReasoningOutput:
        """
        Use Claude API for richer, context-aware reasoning.
        Falls back to rule-based if API call fails.
        """
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)

            prompt = f"""You are Guardian P, an expert AI system that monitors solar PV inverter data for physics violations.

A physics constraint has been violated. Analyse this and respond ONLY with a JSON object.

VIOLATION DETECTED:
- Rule: {violation.rule_id}
- Type: {violation.anomaly_type.value}
- Severity: {violation.severity.value}
- Measured: {violation.measured}
- Expected range: {violation.expected_min:.2f} to {violation.expected_max:.2f}
- Physics message: {violation.message}

INVERTER CONTEXT:
- Inverter ID: {context.get('inverter_id')}
- Timestamp: {context.get('timestamp')}
- Power: {context.get('power_kw')} kW (capacity: {context.get('capacity_kw')} kW)
- Irradiance: {context.get('irradiance_wm2')} W/m²
- Voltage: {context.get('voltage_v')} V
- Current: {context.get('current_a')} A
- Temperature: {context.get('temperature_c')} °C

Respond ONLY with this exact JSON structure, no preamble:
{{
  "root_cause": "one concise sentence describing the most likely physical cause",
  "supporting_facts": ["fact 1", "fact 2", "fact 3"],
  "recommended_actions": ["action 1 (most urgent first)", "action 2", "action 3"]
}}"""

            response = client.messages.create(
                model      = "claude-sonnet-4-20250514",
                max_tokens = 600,
                messages   = [{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            parsed = json.loads(raw)
            confidence = self.get_confidence(violation.anomaly_type, violation.confidence)
            clean_action = (
                "DATA BLOCKED — withheld from downstream AI to prevent hallucination cascade."
                if violation.severity == Severity.CRITICAL
                else "DATA FLAGGED — forwarded with reduced confidence weight."
            )
            return ReasoningOutput(
                anomaly_type        = violation.anomaly_type.value,
                severity            = violation.severity.value,
                confidence_score    = confidence,
                root_cause          = parsed.get("root_cause", violation.message),
                supporting_facts    = parsed.get("supporting_facts", []),
                recommended_actions = parsed.get("recommended_actions", []),
                urgency             = URGENCY_MAP.get(violation.severity, "Review at next opportunity"),
                clean_data_action   = clean_action,
                reasoning_source    = "ai_api",
            )

        except Exception as e:
            # Graceful degradation — never fail silently
            logger.warning("AI API unavailable (%s), using rule-based fallback.", e)
            return self._build_rule_based_output(violation)

    def process(self, result: AnalysisResult) -> AlertPackage:
        """
        Main entry point. Takes an AnalysisResult, produces a full AlertPackage.
        """
        import uuid
        dp = result.data_point
        context = {
            "inverter_id":   dp.inverter_id,
            "timestamp":     dp.timestamp,
            "power_kw":      dp.power_kw,
            "capacity_kw":   dp.capacity_kw,
            "irradiance_wm2": dp.irradiance_wm2,
            "voltage_v":     dp.voltage_v,
            "current_a":     dp.current_a,
            "temperature_c": dp.temperature_c,
        }

        reasoning_outputs = []
        for violation in result.violations:
            if self.use_ai:
                ro = self._build_ai_output(violation, context)
            else:
                ro = self._build_rule_based_output(violation)
            reasoning_outputs.append(ro)

        # Block data from downstream if any CRITICAL violation exists
        is_blocked = any(v.severity == Severity.CRITICAL for v in result.violations)

        return AlertPackage(
            alert_id    = str(uuid.uuid4())[:8],
            timestamp   = dp.timestamp,
            inverter_id = dp.inverter_id,
            is_blocked  = is_blocked,
            reasoning   = reasoning_outputs,
            raw_violations = [
                {
                    "type":     v.anomaly_type.value,
                    "severity": v.severity.value,
                    "rule_id":  v.rule_id,
                    "message":  v.message,
                    "measured": v.measured,
                    "expected_min": v.expected_min,
                    "expected_max": v.expected_max,
                    "confidence": v.confidence,
                }
                for v in result.violations
            ],
        )
