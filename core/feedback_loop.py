"""
Guardian P — Self-Learning Feedback Loop
=========================================
Priority 4: Operator feedback drives automatic threshold recalibration.
No retraining required — adjustments are incremental and persistent.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
import json
import logging
import os

from core.physics_engine import AnomalyType
from core.reasoning_engine import ReasoningEngine

logger = logging.getLogger(__name__)


@dataclass
class FeedbackRecord:
    alert_id:      str
    inverter_id:   str
    anomaly_type:  str
    timestamp:     str
    is_false_positive: bool
    operator_note: Optional[str] = None


@dataclass
class LearningStats:
    total_feedback:     int = 0
    confirmed_correct:  int = 0
    false_positives:    int = 0
    precision:          float = 1.0   # confirmed / (confirmed + FP)
    adjustments:        dict = field(default_factory=dict)


class FeedbackLoop:
    """
    Receives operator feedback on alerts and updates the ReasoningEngine's
    confidence baselines in real time.

    Persistence: feedback log is written to disk as JSONL so that
    confidence state survives service restarts.
    """

    def __init__(self, reasoning_engine: ReasoningEngine, log_path: str = "data/feedback.jsonl"):
        self.engine   = reasoning_engine
        self.log_path = log_path
        self._records: list[FeedbackRecord] = []
        self._load_existing_feedback()

    def _load_existing_feedback(self):
        """Replay historical feedback on startup to restore learned state."""
        if not os.path.exists(self.log_path):
            return
        try:
            with open(self.log_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    rec = FeedbackRecord(**data)
                    self._records.append(rec)
                    # Replay the learning signal
                    try:
                        atype = AnomalyType(rec.anomaly_type)
                        self.engine.apply_feedback(atype, rec.is_false_positive)
                    except ValueError:
                        pass
            logger.info("Replayed %d historical feedback records.", len(self._records))
        except Exception as e:
            logger.warning("Could not load feedback log: %s", e)

    def _persist(self, record: FeedbackRecord):
        """Append one feedback record to disk log."""
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(asdict(record)) + "\n")
        except Exception as e:
            logger.warning("Could not persist feedback record: %s", e)

    def submit(
        self,
        alert_id:          str,
        inverter_id:       str,
        anomaly_type:      str,
        is_false_positive: bool,
        operator_note:     Optional[str] = None,
    ) -> dict:
        """
        Main feedback entry point.
        Returns updated confidence state for the affected anomaly type.
        """
        record = FeedbackRecord(
            alert_id          = alert_id,
            inverter_id       = inverter_id,
            anomaly_type      = anomaly_type,
            timestamp         = datetime.utcnow().isoformat(),
            is_false_positive = is_false_positive,
            operator_note     = operator_note,
        )

        self._records.append(record)
        self._persist(record)

        # Apply learning signal to engine
        try:
            atype = AnomalyType(anomaly_type)
            self.engine.apply_feedback(atype, is_false_positive)
        except ValueError:
            pass

        # Return updated state for this anomaly type
        state = self.engine.get_learning_state()
        updated = state.get(anomaly_type, {})

        return {
            "status":       "accepted",
            "alert_id":     alert_id,
            "anomaly_type": anomaly_type,
            "feedback":     "false_positive" if is_false_positive else "confirmed",
            "updated_confidence": updated.get("effective"),
            "total_feedback_count": len(self._records),
        }

    def get_stats(self) -> LearningStats:
        total = len(self._records)
        fp    = sum(1 for r in self._records if r.is_false_positive)
        ok    = total - fp
        precision = ok / total if total > 0 else 1.0
        return LearningStats(
            total_feedback    = total,
            confirmed_correct = ok,
            false_positives   = fp,
            precision         = round(precision, 3),
            adjustments       = self.engine.get_learning_state(),
        )
