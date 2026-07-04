#!/usr/bin/env python3
"""
Temporal Coverage Engine — OSINT DD Dashboard Backend.

Evaluates how well a screening result covers a subject's temporal profile
(e.g. employment history, address history, corporate affiliations over time).
The engine detects gaps in coverage and flags periods that may warrant
additional investigation.

Architecture:
    - TemporalRecord: Represents a dated fact about a subject.
    - TemporalProfile: Collection of all known dated facts for a subject.
    - CoverageGap: Represents a period with no known data.
    - CoverageResult: Aggregated coverage metrics and gap analysis.
    - TemporalCoverageEngine: Main engine that evaluates coverage.

Gap Detection:
    - Identifies continuous periods with no temporal records.
    - Flags gaps longer than a configurable threshold.
    - Supports custom gap thresholds per module type.

Scoring:
    - Coverage ratio: known-months / total-months in the evaluation window.
    - Recency-weighted coverage: recent months weighted more heavily.
    - Module-specific coverage scores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default gap threshold (days) — gaps longer than this are flagged
DEFAULT_GAP_THRESHOLD_DAYS: int = 365

# Default evaluation window (days) — how far back to look
DEFAULT_EVALUATION_WINDOW_DAYS: int = 365 * 5  # 5 years

# Minimum coverage ratio to be considered "good"
MIN_GOOD_COVERAGE_RATIO: float = 0.6

# Weight for recency in the weighted coverage calculation
RECENCY_WEIGHT_EXPONENT: float = 0.5


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TemporalRecordType(str, Enum):
    """Types of temporal records."""

    EMPLOYMENT = "employment"
    ADDRESS = "address"
    EDUCATION = "education"
    CORPORATE_ROLE = "corporate_role"
    SANCTIONS = "sanctions"
    LITIGATION = "litigation"
    MEDIA_MENTION = "media_mention"
    TRAVEL = "travel"
    OTHER = "other"


@dataclass
class TemporalRecord:
    """A single dated fact about a subject.

    Attributes:
        record_type: Category of the record.
        start_date: When the fact began (may be approximate).
        end_date: When the fact ended (None if ongoing).
        description: Human-readable description.
        source: Data source that provided this record.
        confidence: Confidence level (0.0-1.0).
    """

    record_type: TemporalRecordType
    start_date: date
    end_date: Optional[date] = None
    description: str = ""
    source: str = ""
    confidence: float = 1.0

    def duration_days(self) -> int:
        """Return the duration in days."""
        end = self.end_date or date.today()
        return (end - self.start_date).days

    def is_ongoing(self) -> bool:
        """Return True if the record has no end date."""
        return self.end_date is None

    def overlaps(self, other: TemporalRecord) -> bool:
        """Check if this record overlaps with another."""
        self_end = self.end_date or date.today()
        other_end = other.end_date or date.today()
        return self.start_date <= other_end and other.start_date <= self_end


@dataclass
class TemporalProfile:
    """Collection of all known temporal records for a subject."""

    subject_name: str = ""
    records: List[TemporalRecord] = field(default_factory=list)

    def add_record(self, record: TemporalRecord) -> None:
        """Add a record to the profile."""
        self.records.append(record)

    def get_records_by_type(self, record_type: TemporalRecordType) -> List[TemporalRecord]:
        """Get all records of a specific type."""
        return [r for r in self.records if r.record_type == record_type]

    def get_date_range(self) -> Tuple[Optional[date], Optional[date]]:
        """Get the overall date range of all records."""
        if not self.records:
            return None, None
        starts = [r.start_date for r in self.records]
        ends = [r.end_date for r in self.records if r.end_date is not None]
        return min(starts), max(ends) if ends else date.today()

    def sort_records(self) -> None:
        """Sort records by start date (ascending)."""
        self.records.sort(key=lambda r: r.start_date)


@dataclass
class CoverageGap:
    """A period with no temporal coverage."""

    gap_start: date
    gap_end: date
    duration_days: int = 0
    preceding_record: Optional[TemporalRecord] = None
    following_record: Optional[TemporalRecord] = None

    def __post_init__(self) -> None:
        self.duration_days = (self.gap_end - self.gap_start).days


@dataclass
class ModuleCoverage:
    """Coverage metrics for a single module/record type."""

    module: str
    total_records: int = 0
    coverage_ratio: float = 0.0
    weighted_coverage_ratio: float = 0.0
    gaps: List[CoverageGap] = field(default_factory=list)
    flagged_gaps: List[CoverageGap] = field(default_factory=list)
    score: float = 0.0


@dataclass
class CoverageResult:
    """Aggregated coverage evaluation result."""

    subject_name: str = ""
    evaluation_start: Optional[date] = None
    evaluation_end: Optional[date] = None
    overall_coverage_ratio: float = 0.0
    weighted_coverage_ratio: float = 0.0
    total_gaps: int = 0
    flagged_gaps: int = 0
    module_results: List[ModuleCoverage] = field(default_factory=list)
    gaps: List[CoverageGap] = field(default_factory=list)
    risk_assessment: str = ""
    score: float = 0.0
    recommendations: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TemporalCoverageEngine
# ---------------------------------------------------------------------------

class TemporalCoverageEngine:
    """Evaluates temporal coverage of screening results.

    Usage:
        >>> engine = TemporalCoverageEngine()
        >>> profile = TemporalProfile(subject_name="John Doe")
        >>> profile.add_record(TemporalRecord(
        ...     record_type=TemporalRecordType.EMPLOYMENT,
        ...     start_date=date(2015, 1, 1),
        ...     end_date=date(2020, 12, 31),
        ...     description="CEO at Acme Corp",
        ... ))
        >>> result = engine.evaluate_coverage(profile)
        >>> print(f"Coverage: {result.overall_coverage_ratio:.2%}")
    """

    def __init__(
        self,
        gap_threshold_days: int = DEFAULT_GAP_THRESHOLD_DAYS,
        evaluation_window_days: int = DEFAULT_EVALUATION_WINDOW_DAYS,
    ) -> None:
        self.gap_threshold_days = gap_threshold_days
        self.evaluation_window_days = evaluation_window_days

    def evaluate_coverage(self, profile: TemporalProfile) -> CoverageResult:
        """Evaluate the temporal coverage of *profile*.

        Args:
            profile: The subject's temporal profile.

        Returns:
            CoverageResult with metrics, gaps, and recommendations.
        """
        profile.sort_records()

        evaluation_end = date.today()
        evaluation_start = evaluation_end - timedelta(days=self.evaluation_window_days)

        # Overall coverage
        overall_ratio = self._calculate_coverage_ratio(
            profile.records, evaluation_start, evaluation_end,
        )
        weighted_ratio = self._calculate_weighted_coverage_ratio(
            profile.records, evaluation_start, evaluation_end,
        )

        # Find gaps
        all_gaps = self._find_gaps(profile.records, evaluation_start, evaluation_end)
        flagged_gaps = [g for g in all_gaps if g.duration_days >= self.gap_threshold_days]

        # Module-specific coverage
        module_results: List[ModuleCoverage] = []
        for record_type in TemporalRecordType:
            records = profile.get_records_by_type(record_type)
            if not records:
                continue

            ratio = self._calculate_coverage_ratio(records, evaluation_start, evaluation_end)
            weighted = self._calculate_weighted_coverage_ratio(records, evaluation_start, evaluation_end)
            gaps = self._find_gaps(records, evaluation_start, evaluation_end)
            flagged = [g for g in gaps if g.duration_days >= self.gap_threshold_days]

            module_results.append(
                ModuleCoverage(
                    module=record_type.value,
                    total_records=len(records),
                    coverage_ratio=ratio,
                    weighted_coverage_ratio=weighted,
                    gaps=gaps,
                    flagged_gaps=flagged,
                    score=self._module_score(ratio, len(flagged)),
                )
            )

        # Risk assessment
        risk = self._assess_risk(overall_ratio, len(flagged_gaps), module_results)

        # Recommendations
        recommendations = self._generate_recommendations(
            overall_ratio, flagged_gaps, module_results,
        )

        return CoverageResult(
            subject_name=profile.subject_name,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            overall_coverage_ratio=overall_ratio,
            weighted_coverage_ratio=weighted_ratio,
            total_gaps=len(all_gaps),
            flagged_gaps=len(flagged_gaps),
            module_results=module_results,
            gaps=all_gaps,
            risk_assessment=risk,
            score=self._overall_score(overall_ratio, len(flagged_gaps), module_results),
            recommendations=recommendations,
        )

    # --- Calculation methods ---------------------------------------------

    def _calculate_coverage_ratio(
        self,
        records: List[TemporalRecord],
        eval_start: date,
        eval_end: date,
    ) -> float:
        """Calculate the simple coverage ratio.

        Coverage ratio = known_days / total_days in the evaluation window.
        """
        if not records:
            return 0.0

        total_days = (eval_end - eval_start).days
        if total_days <= 0:
            return 0.0

        # Build a set of covered days (inefficient but simple)
        covered_days: Set[int] = set()
        for record in records:
            start = max(record.start_date, eval_start)
            end = min(record.end_date or eval_end, eval_end)
            if start <= end:
                for day_offset in range((end - start).days + 1):
                    covered_days.add((start + timedelta(days=day_offset) - eval_start).days)

        return len(covered_days) / total_days

    def _calculate_weighted_coverage_ratio(
        self,
        records: List[TemporalRecord],
        eval_start: date,
        eval_end: date,
    ) -> float:
        """Calculate recency-weighted coverage ratio.

        Recent months are weighted more heavily than distant ones using
        an exponential decay function.
        """
        if not records:
            return 0.0

        total_days = (eval_end - eval_start).days
        if total_days <= 0:
            return 0.0

        weighted_covered: float = 0.0
        weighted_total: float = 0.0

        for day_offset in range(total_days):
            current_date = eval_start + timedelta(days=day_offset)
            days_from_end = (eval_end - current_date).days
            weight = (days_from_end / total_days) ** RECENCY_WEIGHT_EXPONENT
            weighted_total += weight

            # Check if this day is covered by any record
            covered = any(
                record.start_date <= current_date <= (record.end_date or eval_end)
                for record in records
            )
            if covered:
                weighted_covered += weight

        return weighted_covered / weighted_total if weighted_total > 0 else 0.0

    def _find_gaps(
        self,
        records: List[TemporalRecord],
        eval_start: date,
        eval_end: date,
    ) -> List[CoverageGap]:
        """Find coverage gaps in the evaluation window.

        A gap is a continuous period where no record covers any day.
        """
        if not records:
            return [CoverageGap(gap_start=eval_start, gap_end=eval_end)]

        gaps: List[CoverageGap] = []

        # Check for gap at the beginning
        first_start = min(r.start_date for r in records)
        if first_start > eval_start:
            gaps.append(CoverageGap(
                gap_start=eval_start,
                gap_end=first_start,
                preceding_record=None,
                following_record=min(records, key=lambda r: r.start_date),
            ))

        # Sort records by start date
        sorted_records = sorted(records, key=lambda r: r.start_date)

        # Find gaps between records
        for i in range(len(sorted_records) - 1):
            current_end = sorted_records[i].end_date
            next_start = sorted_records[i + 1].start_date

            if current_end is None:
                continue  # Ongoing record, no gap

            if current_end < next_start:
                gaps.append(CoverageGap(
                    gap_start=current_end,
                    gap_end=next_start,
                    preceding_record=sorted_records[i],
                    following_record=sorted_records[i + 1],
                ))

        # Check for gap at the end
        last_end = max(
            (r.end_date for r in records if r.end_date is not None),
            default=eval_start,
        )
        if last_end < eval_end:
            # Only flag if there's no ongoing record
            has_ongoing = any(r.is_ongoing() for r in records)
            if not has_ongoing:
                gaps.append(CoverageGap(
                    gap_start=last_end,
                    gap_end=eval_end,
                    preceding_record=max(
                        (r for r in records if r.end_date is not None),
                        key=lambda r: r.end_date,
                        default=None,
                    ),
                    following_record=None,
                ))

        return gaps

    # --- Scoring methods -------------------------------------------------

    @staticmethod
    def _module_score(coverage_ratio: float, flagged_gap_count: int) -> float:
        """Calculate a module-specific score.

        Score = coverage_ratio * (1 - 0.1 * flagged_gap_count)
        """
        gap_penalty = min(0.5, flagged_gap_count * 0.1)
        return max(0.0, coverage_ratio * (1.0 - gap_penalty))

    @staticmethod
    def _overall_score(
        coverage_ratio: float,
        flagged_gap_count: int,
        module_results: List[ModuleCoverage],
    ) -> float:
        """Calculate the overall coverage score."""
        gap_penalty = min(0.5, flagged_gap_count * 0.05)
        module_avg = (
            sum(m.score for m in module_results) / len(module_results)
            if module_results else 0.0
        )
        return max(0.0, min(1.0, coverage_ratio * (1.0 - gap_penalty) * 0.5 + module_avg * 0.5))

    # --- Risk assessment -------------------------------------------------

    @staticmethod
    def _assess_risk(
        coverage_ratio: float,
        flagged_gap_count: int,
        module_results: List[ModuleCoverage],
    ) -> str:
        """Generate a human-readable risk assessment."""
        if coverage_ratio >= 0.8 and flagged_gap_count == 0:
            return "LOW: Good temporal coverage with no significant gaps."
        if coverage_ratio >= 0.6 and flagged_gap_count <= 1:
            return "MEDIUM: Adequate coverage with minor gaps."
        if coverage_ratio >= 0.4:
            return "HIGH: Significant gaps in temporal coverage detected."
        return "CRITICAL: Severe gaps in temporal coverage. Immediate investigation recommended."

    # --- Recommendations -------------------------------------------------

    @staticmethod
    def _generate_recommendations(
        coverage_ratio: float,
        flagged_gaps: List[CoverageGap],
        module_results: List[ModuleCoverage],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations: List[str] = []

        if coverage_ratio < MIN_GOOD_COVERAGE_RATIO:
            recommendations.append(
                f"Overall coverage ({coverage_ratio:.1%}) is below the recommended threshold "
                f"({MIN_GOOD_COVERAGE_RATIO:.1%}). Consider expanding the data sources."
            )

        for gap in flagged_gaps:
            recommendations.append(
                f"Gap detected: {gap.gap_start} to {gap.gap_end} "
                f"({gap.duration_days} days). Investigate this period."
            )

        for module in module_results:
            if module.coverage_ratio < 0.3:
                recommendations.append(
                    f"{module.module}: Very low coverage ({module.coverage_ratio:.1%}). "
                    f"Consider adding additional data sources for this module."
                )

        return recommendations


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def evaluate_temporal_coverage(
    records: List[TemporalRecord],
    subject_name: str = "",
    gap_threshold_days: int = DEFAULT_GAP_THRESHOLD_DAYS,
    evaluation_window_days: int = DEFAULT_EVALUATION_WINDOW_DAYS,
) -> CoverageResult:
    """One-shot convenience function to evaluate temporal coverage.

    Args:
        records: List of temporal records to evaluate.
        subject_name: Name of the subject (for display).
        gap_threshold_days: Gaps longer than this are flagged.
        evaluation_window_days: How far back to evaluate.

    Returns:
        CoverageResult with all metrics and recommendations.
    """
    profile = TemporalProfile(subject_name=subject_name)
    for record in records:
        profile.add_record(record)

    engine = TemporalCoverageEngine(
        gap_threshold_days=gap_threshold_days,
        evaluation_window_days=evaluation_window_days,
    )
    return engine.evaluate_coverage(profile)
