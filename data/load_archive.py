"""
Guardian P — Archive Data Loader
=================================
Reads the four CSV files from ~/Desktop/archive, merges generation data
with weather sensor data per plant, maps every row to the DataPoint schema,
and runs the physics engine over each record.

Auto-detection (no manual config needed):
  • IRRADIATION unit  — kW/m² vs W/m² detected from max observed value
  • DC_POWER unit     — W vs kW detected from DC/AC ratio
  • capacity_kw       — derived from per-inverter p95 peak AC + 10% headroom

Usage:
    cd ~/Desktop/guardian_p
    python data/load_archive.py
"""

import csv
import logging
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Make sure the project root is on sys.path ──────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.physics_engine import DataPoint, analyse, AnalysisResult

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
ARCHIVE = Path.home() / "Desktop" / "archive"

PLANT_FILES = {
    "plant_1": {
        "generation": ARCHIVE / "Plant_1_Generation_Data.csv",
        "weather":    ARCHIVE / "Plant_1_Weather_Sensor_Data.csv",
    },
    "plant_2": {
        "generation": ARCHIVE / "Plant_2_Generation_Data.csv",
        "weather":    ARCHIVE / "Plant_2_Weather_Sensor_Data.csv",
    },
}


# ── Date parsing ───────────────────────────────────────────────────────────

def _parse_dt(raw: str) -> str:
    """
    Normalise the two date formats found in the CSVs to ISO-8601.
    Returns the raw string as-is if parsing fails, so callers can
    detect the failure with a downstream dict.get() miss rather than
    an uncaught exception.
    """
    if not raw or not isinstance(raw, str):
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    for fmt in ("%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return raw  # return as-is; downstream weather_index.get() will return None


# ── Auto-detection: irradiance unit ────────────────────────────────────────

def _detect_irradiance_scale(path: Path) -> float:
    """
    Return the multiplier that converts the raw IRRADIATION column to W/m².

    Decision tree (based on observed maximum non-zero value):
      max < 2    → stored as kW/m²              → ×1000
      max < 10   → stored as normalised fraction → ×100
      max < 50   → stored as ×0.1 W/m²          → ×10
      max ≥ 50   → already W/m²                 → ×1

    Physical ceiling: ~1361 W/m² (solar constant at top of atmosphere).
    Ground-level peak is typically 900–1100 W/m².
    """
    max_val = 0.0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            v = _safe_float(row.get("IRRADIATION"), 0.0)
            if v > max_val:
                max_val = v

    if max_val == 0:
        scale = 1.0
    elif max_val < 2:
        scale = 1000.0
    elif max_val < 10:
        scale = 100.0
    elif max_val < 50:
        scale = 10.0
    else:
        scale = 1.0

    peak_wm2 = max_val * scale
    # Sanity check: result must be within physical bounds
    if not (0 < peak_wm2 <= 1400):
        logger.warning("Irradiance peak %.1f W/m² is outside physical range (0–1400). "
                       "Check sensor units manually.", peak_wm2)

    logger.info("Irradiance unit: raw max=%.4f  →  ×%.0f  (peak ≈ %.1f W/m²)",
                max_val, scale, peak_wm2)
    return scale


# ── Auto-detection: DC_POWER unit ──────────────────────────────────────────

def _detect_dc_power_scale(path: Path) -> float:
    """
    Return the divisor that converts the raw DC_POWER column to kW.

    Method: compute the median DC/AC ratio across all rows where both
    columns are > 0.  A physically healthy inverter has:
      DC_POWER_kW ≈ AC_POWER_kW / η   (η ≈ 0.95–0.98)
    so DC/AC should be slightly > 1 (e.g. 1.02–1.10).

    If the observed median ratio is >> 2, the DC column is almost certainly
    in a different unit (e.g. Plant_1 stores DC in W → ratio ≈ 10).
    We round to the nearest power of 10 to get a clean divisor.

    Returns a divisor (apply as:  dc_kw = raw_dc / divisor).
    """
    ratios = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ac = _safe_float(row.get("AC_POWER"), 0.0)
            dc = _safe_float(row.get("DC_POWER"),  0.0)
            if ac > 1 and dc > 0:           # only use rows with meaningful output
                ratios.append(dc / ac)

    if not ratios:
        logger.warning("DC_POWER unit: no valid DC/AC pairs found — assuming kW (÷1)")
        return 1.0

    ratios.sort()
    median_ratio = ratios[len(ratios) // 2]

    # Guard: if DC ≈ AC or DC < AC (healthy η, or DC mis-scaled small),
    # median_ratio can be < 1.  log10 of a value < 1 is negative, which
    # would produce a divisor < 1 and *amplify* DC values instead of
    # shrinking them.  We clamp to a minimum divisor of 1 (i.e. "assume kW").
    import math
    if median_ratio < 1.0:
        logger.info("DC_POWER unit: median DC/AC=%.2f < 1.0 → ÷1 (no conversion)", median_ratio)
        return 1.0

    magnitude = 10 ** round(math.log10(median_ratio))
    divisor = float(magnitude)

    unit_label = "W" if divisor >= 1000 else ("×0.1 kW" if divisor == 10 else "kW")
    logger.info("DC_POWER unit: median DC/AC=%.2f  →  ÷%.0f  (stored as %s)",
                median_ratio, divisor, unit_label)
    return divisor


# ── Auto-detection: per-inverter capacity ──────────────────────────────────

def _detect_capacity_kw(path: Path, dc_divisor: float) -> float:
    """
    Estimate the rated capacity of a single inverter (kW).

    Method:
      1. Collect the maximum AC_POWER seen for each SOURCE_KEY (inverter).
      2. Take the p95 of those per-inverter peaks  (guards against a small
         number of abnormally high outlier readings skewing the result).
      3. Add 10% headroom — real nameplate capacity is always a bit above
         the observed operating peak.

    The 10% headroom means we will almost never trigger OVER_POWER for
    a physically normal reading, while still catching genuine meter faults
    (readings 2× or more above the estimated nameplate).
    """
    inv_peak: dict[str, float] = defaultdict(float)

    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ac  = _safe_float(row.get("AC_POWER"), 0.0)
            key = row.get("SOURCE_KEY") or "unknown"
            if ac > inv_peak[key]:
                inv_peak[key] = ac

    if not inv_peak:
        logger.warning("Capacity: no data — defaulting to 100 kW")
        return 100.0

    peaks = sorted(inv_peak.values())
    n = len(peaks)
    p95_peak = peaks[min(int(n * 0.95), n - 1)]
    capacity = round(p95_peak * 1.10, 1)   # +10% nameplate headroom

    logger.info("Capacity/inv: %d inverters  p95 peak=%.1f kW  →  rated ≈ %.1f kW (+10%%)",
                n, p95_peak, capacity)
    return capacity


# ── Weather loader ─────────────────────────────────────────────────────────

def load_weather(path: Path) -> tuple[dict[str, dict], float]:
    """
    Returns (index, irr_scale).
      index     : timestamp_iso → weather row dict
      irr_scale : multiply raw IRRADIATION by this to get W/m²
    """
    irr_scale = _detect_irradiance_scale(path)
    index: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ts = _parse_dt(row["DATE_TIME"])
            index[ts] = row
    return index, irr_scale


# ── Generation loader ──────────────────────────────────────────────────────

def load_generation(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ── DataPoint builder ──────────────────────────────────────────────────────

def _safe_float(value, default: float = 0.0) -> float:
    """
    Convert *value* to float, returning *default* on any failure.
    Handles None, empty string, 'N/A', 'nan', and other non-numeric strings
    that appear in real-world CSV exports.
    """
    if value is None:
        return default
    try:
        result = float(value)
        # Treat IEEE NaN/Inf as missing — they would propagate silently
        # through arithmetic and produce nonsensical physics results.
        if not math.isfinite(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def build_datapoint(
    gen_row:     dict,
    weather:     Optional[dict],
    plant_id:    str,
    irr_scale:   float,
    dc_divisor:  float,
    capacity_kw: float,
) -> DataPoint:
    """
    Map one generation row + matching weather row to a DataPoint.

    Power signal priority:  AC > DC (AC is the inverter output we care about;
    DC is used only to derive the string-level current estimate).

    Voltage / current proxy
    -----------------------
    The archive does not record DC bus voltage or string current directly.
    We use a proxy Vmp = 0.80 × Voc_stc, which represents the typical
    maximum-power-point voltage under real operating conditions
    (temperature derating from STC 25°C pushes Vmp below Voc).

    The physics engine's default Voc is 400 V (single-module STC value).
    For this dataset the effective string Voc is also 400 V (the DataPoint
    default), so we keep voltage_v within the legal operating band:

        lower_limit = Voc × 0.25 = 100 V
        upper_limit = Voc × 1.05 = 420 V
        Vmp proxy   = Voc × 0.80 = 320 V  ← used here

    This keeps rule PV-002 (voltage bounds) meaningful without injecting
    spurious OVERVOLTAGE flags from an incorrectly assumed voltage.
    """
    ts = _parse_dt(gen_row.get("DATE_TIME", ""))

    ac_power_kw = _safe_float(gen_row.get("AC_POWER"), 0.0)
    raw_dc      = _safe_float(gen_row.get("DC_POWER"),  0.0)
    dc_power_kw = raw_dc / dc_divisor          # normalised to kW

    # Primary power signal: AC output; fall back to DC when AC is absent
    power_kw = ac_power_kw if ac_power_kw != 0 else dc_power_kw

    # Proxy DC operating voltage: 80% of default Voc (400 V)
    voc_stc           = 400.0          # matches DataPoint default
    vmp_proxy_v       = voc_stc * 0.80   # = 320 V
    estimated_current_a = (dc_power_kw * 1000.0 / vmp_proxy_v) if dc_power_kw > 0 else 0.0

    if weather:
        raw_irr     = _safe_float(weather.get("IRRADIATION"), 0.0)
        irradiance  = raw_irr * irr_scale
        module_temp = _safe_float(weather.get("MODULE_TEMPERATURE"), 25.0)
    else:
        irradiance  = 0.0
        module_temp = 25.0

    return DataPoint(
        timestamp      = ts,
        inverter_id    = f"{plant_id}::{gen_row['SOURCE_KEY']}",
        power_kw       = power_kw,
        irradiance_wm2 = irradiance,
        voltage_v      = vmp_proxy_v,   # within PV-002 legal band
        current_a      = estimated_current_a,
        temperature_c  = module_temp,
        capacity_kw    = capacity_kw,
        voc            = voc_stc,
    )


# ── Main processing loop ───────────────────────────────────────────────────

def process_plant(plant_id: str, files: dict) -> list[AnalysisResult]:
    logger.info("Processing %s", plant_id.upper())

    # --- auto-detect all per-plant parameters before processing rows ---
    weather_index, irr_scale = load_weather(files["weather"])
    dc_divisor  = _detect_dc_power_scale(files["generation"])
    capacity_kw = _detect_capacity_kw(files["generation"], dc_divisor)

    gen_rows = load_generation(files["generation"])
    logger.info("  Generation rows: %d  Weather entries: %d",
                len(gen_rows), len(weather_index))

    results:          list[AnalysisResult]    = []
    prev_by_inverter: dict[str, DataPoint]    = {}
    total_violations  = 0
    matched_weather   = 0

    for gen_row in gen_rows:
        ts      = _parse_dt(gen_row["DATE_TIME"])
        weather = weather_index.get(ts)
        if weather:
            matched_weather += 1

        dp = build_datapoint(
            gen_row, weather, plant_id,
            irr_scale, dc_divisor, capacity_kw,
        )
        prev_dp = prev_by_inverter.get(dp.inverter_id)

        result  = analyse(dp, prev_dp)
        results.append(result)

        prev_by_inverter[dp.inverter_id] = dp
        total_violations += len(result.violations)

    logger.info("  Weather-matched: %d/%d  Violations: %d",
                matched_weather, len(gen_rows), total_violations)
    return results


# ── Summary ────────────────────────────────────────────────────────────────

def summarise(all_results: list[AnalysisResult]) -> None:
    from collections import Counter

    severity_counts:   Counter      = Counter()
    anomaly_counts:    Counter      = Counter()
    flagged_inverters: set[str]     = set()
    sample_violations: list[tuple]  = []

    for r in all_results:
        for v in r.violations:
            severity_counts[v.severity.value] += 1
            anomaly_counts[v.anomaly_type.value] += 1
            flagged_inverters.add(r.data_point.inverter_id)
            if len(sample_violations) < 5:
                sample_violations.append((r.data_point, v))

    total   = len(all_results)
    flagged = sum(1 for r in all_results if not r.is_clean)

    print(f"\n{'='*60}")
    print("  SUMMARY — ALL PLANTS")
    print(f"{'='*60}")
    print(f"  Total data points analysed : {total:,}")
    print(f"  Clean (no violations)      : {total - flagged:,}")
    print(f"  Flagged data points        : {flagged:,}  ({flagged/total*100:.1f}%)")
    print(f"  Unique inverters flagged   : {len(flagged_inverters)}")

    print("\n  Violations by severity:")
    for sev in ("critical", "warning", "info"):
        n = severity_counts.get(sev, 0)
        print(f"    {sev.upper():10s}: {n:,}")

    print("\n  Top anomaly types:")
    for atype, count in anomaly_counts.most_common(10):
        print(f"    {atype:20s}: {count:,}")

    print("\n  Sample violations (first 5):")
    for dp, v in sample_violations:
        print(
            f"    [{v.severity.value.upper():8s}] {dp.inverter_id}  "
            f"{dp.timestamp}  {v.anomaly_type.value}  "
            f"confidence={v.confidence:.2f}"
        )


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    all_results: list[AnalysisResult] = []

    for plant_id, files in PLANT_FILES.items():
        for label, path in files.items():
            if not path.exists():
                logger.error("Missing file (%s): %s", label, path)
                sys.exit(1)

        plant_results = process_plant(plant_id, files)
        all_results.extend(plant_results)

    summarise(all_results)
    logger.info("Done.")


if __name__ == "__main__":
    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt = "%H:%M:%S",
    )
    main()
