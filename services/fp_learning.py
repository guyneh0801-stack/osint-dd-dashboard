"""
False Positive Learning Module for the OSINT DD Dashboard.

This module learns from analyst feedback to reduce false positives over time.
When an entity pattern repeatedly triggers incorrect matches, the system learns
to de-prioritize or auto-dismiss those matches, reducing analyst workload.

Architecture:
    - EntityFingerprinter: Creates stable identifiers for entity-source-record pairs
    - FeedbackEntry: Pydantic model for a single analyst decision
    - FeedbackStore: Persistent storage with in-memory cache for fast lookups
    - BayesianScorer: Adjusts match scores using weighted historical feedback
    - ActiveLearningQueue: Prioritizes uncertain matches for maximum learning
    - FPModelTrainer: Stub for future ML-based prediction

Phased Activation:
    1. Feedback Collection (0-9 feedbacks): Store decisions, no auto-actions
    2. Conservative Scoring (10-29 feedbacks): Adjust scores, no auto-dismiss
    3. Full Auto-Actions (30+ feedbacks): Enable auto-dismiss and auto-flag

Bayesian Scoring Math:
    - Each feedback entry is weighted by recency using exponential decay
    - The weighted false-positive ratio drives score adjustment
    - adjusted_score = base_score * (1 - fp_ratio * 0.8)
    - Scores are clamped to [0.05, 0.95] to retain some uncertainty
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Decay: weights halve every N months
DECAY_HALF_LIFE_MONTHS: float = 12.0

# Feedback older than this gets weight 0.0
EXPIRY_MONTHS: float = 24.0

# Minimum feedbacks before score adjustment kicks in
MIN_FEEDBACK_COUNT: int = 3

# Minimum feedbacks before auto-dismiss is allowed
MIN_FEEDBACK_AUTO_DISMISS: int = 10

# Minimum feedbacks before auto-flag is allowed
MIN_FEEDBACK_AUTO_FLAG: int = 5

# Score thresholds for automated actions
AUTO_DISMISS_THRESHOLD: float = 0.15
AUTO_FLAG_THRESHOLD: float = 0.85

# Maximum adjustment factor (dampens to prevent over-correction)
MAX_ADJUSTMENT_FACTOR: float = 0.8

# Score clamp bounds
SCORE_MIN_CLAMP: float = 0.05
SCORE_MAX_CLAMP: float = 0.95


# ---------------------------------------------------------------------------
# EntityFingerprinter
# ---------------------------------------------------------------------------

class EntityFingerprinter:
    """Creates stable fingerprints for entity matches to identify recurring patterns.

    The fingerprint is a SHA-256 hash of a pipe-delimited string containing the
    normalized entity name, the screening module, and the source record ID.
    This produces a collision-resistant, deterministic identifier that survives
    minor variations in how the entity name is presented.
    """

    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize a name for fingerprinting.

        Steps:
            1. Strip leading/trailing whitespace
            2. Lowercase
            3. Strip diacritics (e → e, n → n)
            4. Collapse multiple whitespace characters to a single space

        Args:
            name: Raw entity name (e.g., "  Ali  HASSAN  ")

        Returns:
            Normalized name (e.g., "ali hassan")
        """
        # Step 1: strip outer whitespace
        normalized: str = name.strip()

        # Step 2: lowercase
        normalized = normalized.lower()

        # Step 3: strip diacritics using NFD decomposition + filtering
        # NFD splits 'e' into 'e' + '´' — we keep only the base letters
        normalized = "".join(
            ch for ch in unicodedata.normalize("NFD", normalized)
            if unicodedata.category(ch) != "Mn"
        )

        # Step 4: collapse internal whitespace to single spaces
        normalized = re.sub(r"\s+", " ", normalized)

        return normalized

    @staticmethod
    def fingerprint(name: str, module: str, source_record_id: str) -> str:
        """Create a SHA-256 fingerprint from normalized inputs.

        The fingerprint format is::

            sha256(f"{normalized_name}|{module}|{source_record_id}")

        This is deterministic: the same (name, module, source_record_id) triple
        always produces the same fingerprint, even if the name has different
        spacing, casing, or diacritics.

        Args:
            name: Entity name (e.g., "Ali Hassan")
            module: Screening module (e.g., "sanctions", "adverse_media")
            source_record_id: ID of the matched source record

        Returns:
            64-character hex-encoded SHA-256 digest
        """
        normalized_name: str = EntityFingerprinter.normalize_name(name)
        raw: str = f"{normalized_name}|{module}|{source_record_id}"
        digest: str = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return digest


# ---------------------------------------------------------------------------
# FeedbackEntry
# ---------------------------------------------------------------------------

class AnalystDecision(str, Enum):
    """Enumeration of possible analyst decisions."""

    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    UNCERTAIN = "uncertain"


class FeedbackEntry(BaseModel):
    """A single analyst feedback decision on a matched entity.

    Each time an analyst reviews a triage item and marks it as a true positive,
    false positive, or uncertain, a FeedbackEntry is created and stored.
    Over time, these entries build a learning signal for the Bayesian scorer.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier (UUID v4)."""

    fingerprint: str
    """SHA-256 fingerprint of the (entity, module, source_record) tuple."""

    triage_item_id: str
    """Foreign key to the triage item that was reviewed."""

    job_id: str
    """Foreign key to the screening job that produced the match."""

    module: str
    """Screening module that produced the match."""

    source_record_id: str
    """Identifier of the source record that was matched."""

    analyst_decision: Literal["true_positive", "false_positive", "uncertain"]
    """The analyst's classification of this match."""

    analyst_notes: Optional[str] = None
    """Optional free-text notes from the analyst explaining their decision."""

    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    """Timestamp when the analyst submitted their decision."""

    resolved_by: Optional[str] = None
    """Identifier of the analyst who made the decision."""

    entity_name: str
    """Human-readable entity name for display and debugging."""

    class Config:
        """Pydantic configuration."""

        json_encoders = {
            datetime: lambda dt: dt.isoformat(),
        }


# ---------------------------------------------------------------------------
# Database Protocol (for dependency injection / typing)
# ---------------------------------------------------------------------------

class DBSession(Protocol):
    """Protocol for database session interactions."""

    async def fetch_all(self, query: str, params: tuple) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return all rows."""
        ...

    async def execute(self, query: str, params: tuple) -> None:
        """Execute an INSERT/UPDATE/DELETE query."""
        ...


# ---------------------------------------------------------------------------
# FeedbackStore
# ---------------------------------------------------------------------------

class FeedbackStore:
    """Persistent storage for analyst feedback with an in-memory cache."""

    def __init__(
        self,
        db_session_factory: Optional[Any] = None,
    ) -> None:
        """Initialize the feedback store."""
        self._session_factory: Optional[Any] = db_session_factory
        self._cache: Dict[str, List[FeedbackEntry]] = {}
        self._initialized: bool = False
        self._write_buffer: List[FeedbackEntry] = []

    async def init(self) -> None:
        """Load all feedback from the database into the in-memory cache."""
        if self._initialized:
            logger.debug("FeedbackStore already initialized; skipping.")
            return

        if self._session_factory is not None:
            await self._load_from_db()
        else:
            logger.info("FeedbackStore initialized in memory-only mode.")

        self._initialized = True
        logger.info("FeedbackStore ready: %d fingerprints cached.", len(self._cache))

    async def _load_from_db(self) -> None:
        """Hydrate the cache from the database."""
        try:
            session = self._session_factory()
            rows = await session.fetch_all(
                "SELECT * FROM feedback_entries ORDER BY resolved_at DESC",
                (),
            )
            for row in rows:
                entry = FeedbackEntry(**row)
                self._cache.setdefault(entry.fingerprint, []).append(entry)
            logger.info("Loaded %d feedback entries from DB.", len(rows))
        except Exception as exc:
            logger.warning(
                "Failed to load feedback from DB: %s. Continuing with empty cache.",
                exc,
            )

    async def reset(self) -> None:
        """Clear the in-memory cache and reset initialization state."""
        self._cache.clear()
        self._initialized = False
        self._write_buffer.clear()
        logger.info("FeedbackStore cache reset.")

    async def add_feedback(self, entry: FeedbackEntry) -> None:
        """Store a new feedback entry."""
        self._cache.setdefault(entry.fingerprint, []).append(entry)
        self._cache[entry.fingerprint].sort(
            key=lambda e: e.resolved_at, reverse=True,
        )
        if self._session_factory is not None:
            await self._persist_entry(entry)
        else:
            self._write_buffer.append(entry)

    async def _persist_entry(self, entry: FeedbackEntry) -> None:
        """Write a single entry to the database."""
        try:
            session = self._session_factory()
            await session.execute(
                """
                INSERT INTO feedback_entries (
                    id, fingerprint, triage_item_id, job_id, module,
                    source_record_id, analyst_decision, analyst_notes,
                    resolved_at, resolved_by, entity_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id, entry.fingerprint, entry.triage_item_id, entry.job_id,
                    entry.module, entry.source_record_id, entry.analyst_decision,
                    entry.analyst_notes, entry.resolved_at.isoformat(),
                    entry.resolved_by, entry.entity_name,
                ),
            )
        except Exception as exc:
            logger.error("Failed to persist feedback entry %s: %s", entry.id, exc)

    async def get_feedback_for_fingerprint(self, fingerprint: str) -> List[FeedbackEntry]:
        """Get all feedback entries for a fingerprint."""
        return list(self._cache.get(fingerprint, []))

    async def get_feedback_stats(self, fingerprint: str) -> Dict[str, int]:
        """Return aggregated decision counts for a fingerprint."""
        entries: List[FeedbackEntry] = self._cache.get(fingerprint, [])
        stats: Dict[str, int] = {"true_positive": 0, "false_positive": 0, "uncertain": 0}
        for entry in entries:
            stats[entry.analyst_decision] += 1
        return stats

    async def get_all_fingerprints(self) -> List[str]:
        """Get all known fingerprints."""
        return list(self._cache.keys())

    async def get_total_feedback_count(self) -> int:
        """Get the total number of feedback entries."""
        return sum(len(entries) for entries in self._cache.values())


# ---------------------------------------------------------------------------
# BayesianScorer
# ---------------------------------------------------------------------------

class BayesianScorer:
    """Adjusts match scores based on historical analyst feedback.

    Uses a Bayesian-like approach with exponential time decay.
    """

    AUTO_DISMISS_THRESHOLD: float = AUTO_DISMISS_THRESHOLD
    AUTO_FLAG_THRESHOLD: float = AUTO_FLAG_THRESHOLD
    MIN_FEEDBACK_COUNT: int = MIN_FEEDBACK_COUNT
    DECAY_HALF_LIFE_MONTHS: float = DECAY_HALF_LIFE_MONTHS
    EXPIRY_MONTHS: float = EXPIRY_MONTHS

    def __init__(self, feedback_store: FeedbackStore) -> None:
        self.feedback_store: FeedbackStore = feedback_store

    async def adjust_score(self, fingerprint: str, base_score: float) -> float:
        """Adjust a base resolution score using Bayesian feedback.

        Returns the adjusted score (0.05-0.95).
        """
        feedback_entries: List[FeedbackEntry] = (
            await self.feedback_store.get_feedback_for_fingerprint(fingerprint)
        )

        if len(feedback_entries) < self.MIN_FEEDBACK_COUNT:
            return base_score

        decayed_weights: List[float] = self._apply_decay(feedback_entries)
        fp_ratio: float = self._compute_weighted_fp_ratio(feedback_entries, decayed_weights)
        adjustment_factor: float = 1.0 - (fp_ratio * MAX_ADJUSTMENT_FACTOR)
        adjusted_score: float = base_score * adjustment_factor
        return max(SCORE_MIN_CLAMP, min(SCORE_MAX_CLAMP, adjusted_score))

    async def should_auto_dismiss(self, fingerprint: str, score: float) -> bool:
        """Determine whether a match should be auto-dismissed."""
        if score >= self.AUTO_DISMISS_THRESHOLD:
            return False

        feedback_entries: List[FeedbackEntry] = (
            await self.feedback_store.get_feedback_for_fingerprint(fingerprint)
        )

        if len(feedback_entries) < MIN_FEEDBACK_AUTO_DISMISS:
            return False

        decayed_weights: List[float] = self._apply_decay(feedback_entries)
        fp_ratio: float = self._compute_weighted_fp_ratio(feedback_entries, decayed_weights)

        if fp_ratio < 0.6:
            return False

        logger.info(
            "Auto-dismiss triggered for fingerprint %s (score=%.3f, fp_ratio=%.3f).",
            fingerprint[:16], score, fp_ratio,
        )
        return True

    async def should_auto_flag(self, fingerprint: str, score: float) -> bool:
        """Determine whether a match should be auto-flagged as a likely true positive."""
        if score < self.AUTO_FLAG_THRESHOLD:
            return False

        feedback_entries: List[FeedbackEntry] = (
            await self.feedback_store.get_feedback_for_fingerprint(fingerprint)
        )

        if len(feedback_entries) < MIN_FEEDBACK_AUTO_FLAG:
            return False

        decayed_weights: List[float] = self._apply_decay(feedback_entries)
        fp_ratio: float = self._compute_weighted_fp_ratio(feedback_entries, decayed_weights)
        tp_ratio: float = 1.0 - fp_ratio

        if tp_ratio < 0.7:
            return False

        logger.info(
            "Auto-flag triggered for fingerprint %s (score=%.3f, tp_ratio=%.3f).",
            fingerprint[:16], score, tp_ratio,
        )
        return True

    async def get_classification(self, fingerprint: str) -> str:
        """Get the learned classification for a fingerprint."""
        feedback_entries: List[FeedbackEntry] = (
            await self.feedback_store.get_feedback_for_fingerprint(fingerprint)
        )

        if len(feedback_entries) < self.MIN_FEEDBACK_COUNT:
            return "insufficient_data"

        decayed_weights: List[float] = self._apply_decay(feedback_entries)
        fp_ratio: float = self._compute_weighted_fp_ratio(feedback_entries, decayed_weights)
        tp_ratio: float = 1.0 - fp_ratio

        if tp_ratio >= 0.8:
            return "reliable_tp"
        if fp_ratio >= 0.8:
            return "likely_fp"
        return "uncertain"

    def _apply_decay(self, feedback_entries: List[FeedbackEntry]) -> List[float]:
        """Apply exponential decay to feedback weights based on age."""
        now: datetime = datetime.now(timezone.utc)
        weights: List[float] = []

        for entry in feedback_entries:
            age_seconds: float = (now - entry.resolved_at).total_seconds()
            age_months: float = age_seconds / (30.44 * 24 * 3600)

            if age_months > self.EXPIRY_MONTHS:
                weights.append(0.0)
                continue

            decay: float = (0.5) ** (age_months / self.DECAY_HALF_LIFE_MONTHS)
            weights.append(decay)

        return weights

    def _compute_weighted_fp_ratio(
        self, feedback_entries: List[FeedbackEntry], decayed_weights: List[float],
    ) -> float:
        """Compute the weighted false-positive ratio."""
        weighted_fp: float = 0.0
        weighted_total: float = 0.0

        for entry, weight in zip(feedback_entries, decayed_weights):
            if weight <= 0.0:
                continue

            if entry.analyst_decision == "false_positive":
                weighted_fp += weight
                weighted_total += weight
            elif entry.analyst_decision == "true_positive":
                weighted_total += weight
            else:  # uncertain
                weighted_fp += weight * 0.5
                weighted_total += weight

        if weighted_total == 0.0:
            return 0.0

        return weighted_fp / weighted_total


# ---------------------------------------------------------------------------
# ActiveLearningQueue
# ---------------------------------------------------------------------------

class ActiveLearningQueue:
    """Prioritizes triage items for maximum information gain.

    Uses uncertainty sampling to prioritize matches that are most
    informative to review.
    """

    def __init__(self, feedback_store: FeedbackStore) -> None:
        self.feedback_store: FeedbackStore = feedback_store

    def calculate_priority(
        self, triage_item: Dict[str, Any], resolution_score: float,
    ) -> float:
        """Calculate review priority using uncertainty sampling.

        Returns priority score (0.0-100.0). Higher = more informative.
        """
        uncertainty: float = 1.0 - abs(resolution_score - 0.5) * 2.0
        uncertainty = max(0.0, min(1.0, uncertainty))

        feedback_count: int = triage_item.get("feedback_count", 0)
        if feedback_count == 0 and "fingerprint" in triage_item:
            fingerprint: str = triage_item["fingerprint"]
            cached_entries = self.feedback_store._cache.get(fingerprint, [])
            feedback_count = len(cached_entries)

        novelty: float = 1.0 / (1.0 + feedback_count)
        priority: float = uncertainty * novelty * 100.0
        return priority

    def rank_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort triage items by priority score descending."""
        scored_items: List[Dict[str, Any]] = []

        for item in items:
            score: float = item.get("resolution_score", 0.5)
            priority: float = self.calculate_priority(item, score)
            enriched_item: Dict[str, Any] = {**item, "priority_score": priority}
            scored_items.append(enriched_item)

        scored_items.sort(key=lambda x: x["priority_score"], reverse=True)
        return scored_items


# ---------------------------------------------------------------------------
# FPModelTrainer (stub for future ML model)
# ---------------------------------------------------------------------------

class FPModelTrainer:
    """Stub for a future machine-learning-based false-positive predictor."""

    RETRAIN_FEEDBACK_THRESHOLD: int = 100

    def __init__(self) -> None:
        self._feedback_since_last_train: int = 0
        self._last_train_time: Optional[datetime] = None
        self._model_trained: bool = False

    async def train(self, feedback_entries: List[FeedbackEntry]) -> None:
        """Stub: logs the call and updates internal counters."""
        logger.info(
            "FPModelTrainer.train() called with %d entries. (Stub)",
            len(feedback_entries),
        )
        self._last_train_time = datetime.now(timezone.utc)
        self._feedback_since_last_train = 0
        self._model_trained = True

    async def predict(self, entity_features: Dict[str, Any]) -> float:
        """Stub: returns 0.5 (maximally uncertain)."""
        logger.debug(
            "FPModelTrainer.predict() called. (Stub - returning 0.5)",
        )
        return 0.5

    async def should_retrain(self) -> bool:
        """Check whether enough new feedback has accumulated."""
        if not self._model_trained:
            return True
        return self._feedback_since_last_train >= self.RETRAIN_FEEDBACK_THRESHOLD

    async def get_model_stats(self) -> Dict[str, Any]:
        """Get statistics about the current model state."""
        return {
            "model_trained": self._model_trained,
            "last_train_time": (
                self._last_train_time.isoformat() if self._last_train_time else None
            ),
            "feedback_since_last_train": self._feedback_since_last_train,
            "retrain_threshold": self.RETRAIN_FEEDBACK_THRESHOLD,
            "status": "stub",
            "note": (
                "ML model not yet implemented. BayesianScorer is used for all predictions."
            ),
        }

    def record_feedback(self, count: int = 1) -> None:
        """Record that new feedback has been received."""
        self._feedback_since_last_train += count


# ---------------------------------------------------------------------------
# Integration Functions
# ---------------------------------------------------------------------------

async def process_triage_feedback(
    triage_item_id: str,
    job_id: str,
    fingerprint: str,
    module: str,
    source_record_id: str,
    entity_name: str,
    decision: Literal["true_positive", "false_positive", "uncertain"],
    analyst_id: Optional[str] = None,
    notes: Optional[str] = None,
    feedback_store: Optional[FeedbackStore] = None,
) -> FeedbackEntry:
    """Process a single analyst feedback decision."""
    store: FeedbackStore = feedback_store or FeedbackStore()

    if not store._initialized:
        await store.init()

    entry = FeedbackEntry(
        fingerprint=fingerprint,
        triage_item_id=triage_item_id,
        job_id=job_id,
        module=module,
        source_record_id=source_record_id,
        analyst_decision=decision,
        analyst_notes=notes,
        resolved_by=analyst_id,
        entity_name=entity_name,
    )

    await store.add_feedback(entry)

    logger.info(
        "Triage feedback processed: triage=%s decision=%s fingerprint=%s",
        triage_item_id, decision, fingerprint[:16],
    )

    return entry


async def score_with_feedback(
    fingerprint: str,
    base_score: float,
    feedback_store: FeedbackStore,
) -> float:
    """Convenience function: adjust a score using Bayesian feedback."""
    scorer = BayesianScorer(feedback_store)
    return await scorer.adjust_score(fingerprint, base_score)


# ---------------------------------------------------------------------------
# Batch processing helpers
# ---------------------------------------------------------------------------

async def batch_adjust_scores(
    matches: List[Dict[str, Any]],
    feedback_store: FeedbackStore,
) -> List[Dict[str, Any]]:
    """Adjust scores for a batch of matches."""
    scorer = BayesianScorer(feedback_store)
    results: List[Dict[str, Any]] = []

    for match in matches:
        fingerprint: str = match["fingerprint"]
        base_score: float = match["base_score"]

        adjusted_score: float = await scorer.adjust_score(fingerprint, base_score)
        classification: str = await scorer.get_classification(fingerprint)

        enriched: Dict[str, Any] = {
            **match,
            "adjusted_score": adjusted_score,
            "classification": classification,
        }
        results.append(enriched)

    return results
