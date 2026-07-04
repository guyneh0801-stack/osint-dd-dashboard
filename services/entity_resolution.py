#!/usr/bin/env python3
"""
Entity Resolution Module — OSINT DD Dashboard Backend.

This module scores how likely a matched record is to actually refer to the
subject being screened. It compares secondary attributes (DOB, nationality,
address, ID numbers) between the subject profile and each candidate match,
producing a weighted resolution score and human-readable explanation.

Architecture
------------
    EntityProfile          – Normalised representation of a person / entity.
    AttributeMatcher       – Static helpers that score individual attributes.
    ResolutionScorer       – Multi-attribute weighted scorer with auto-reweighting.
    ResolutionResult       – Structured output for a single match.
    ResolutionBatchProcessor – Async batch pipeline that ranks candidates.

Typical flow
------------
    subject = EntityProfile.from_screening_input(
        name_en="John Smith", dob="1980-01-15", nationalities=["US"]
    )
    matches = [...]                       # from sanctions / screening API
    processor = ResolutionBatchProcessor()
    results = await processor.process_matches(subject, matches)
    # results sorted by score desc; no_match entries already filtered out
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Normalisation utilities
# ---------------------------------------------------------------------------

def _strip_diacritics(text: str) -> str:
    """Remove diacritical marks from *text* while keeping base letters.

    Example
    -------
    >>> _strip_diacritics("José")
    'Jose'
    """
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _normalise_text(text: Optional[str]) -> Optional[str]:
    """Lower-case, strip diacritics, collapse whitespace.

    Returns *None* when the input is *None* or empty after stripping.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    text = text.lower()
    text = _strip_diacritics(text)
    text = re.sub(r"\s+", " ", text)
    return text


def _normalise_date(dob: Optional[str]) -> Optional[str]:
    """Normalise a date string to ``YYYY-MM-DD``, ``YYYY-MM``, or ``YYYY``.

    Accepted inputs
    ---------------
    - ``YYYY-MM-DD``   → returned as-is (after normalisation)
    - ``YYYY/MM/DD``   → converted to dashes
    - ``DD-MM-YYYY``   → flipped to ``YYYY-MM-DD``
    - ``YYYY-MM``      → returned as-is
    - ``YYYY``         → returned as-is
    - anything else    → *None*
    """
    if dob is None:
        return None
    dob = dob.strip()
    if not dob:
        return None

    # Normalise separators to hyphens
    dob = re.sub(r"[./]", "-", dob)

    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
        return dob

    # DD-MM-YYYY → YYYY-MM-DD
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", dob)
    if m:
        dd, mm, yyyy = m.groups()
        return f"{yyyy}-{mm}-{dd}"

    # YYYY-MM
    if re.fullmatch(r"\d{4}-\d{2}", dob):
        return dob

    # YYYY
    if re.fullmatch(r"\d{4}", dob):
        return dob

    return None


def _extract_year_month(date_str: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(year, month)`` from a normalised date string.

    ``month`` may be *None* when only the year is available.
    """
    if date_str is None:
        return None, None
    parts = date_str.split("-")
    year = parts[0] if parts else None
    month = parts[1] if len(parts) >= 2 else None
    return year, month


def _jaccard_index(list1: List[str], list2: List[str]) -> float:
    """Compute the Jaccard similarity index between two lists of strings.

    The comparison is case-insensitive after normalisation.
    """
    set1: Set[str] = {item.lower().strip() for item in list1 if item.strip()}
    set2: Set[str] = {item.lower().strip() for item in list2 if item.strip()}
    if not set1 and not set2:
        return 0.0
    if not set1 or not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# EntityProfile
# ---------------------------------------------------------------------------

class EntityProfile(BaseModel):
    """Normalised profile of a person or entity used for resolution scoring.

    All string fields are stored in normalised form (lower-case, no diacritics,
    collapsed whitespace).  Call :py:meth:`normalise` after construction (or use
    the ``from_*`` factory methods) to guarantee consistency.
    """

    name: str = Field(..., description="Primary name (normalised)")
    aliases: List[str] = Field(default_factory=list, description="Known aliases (normalised)")
    date_of_birth: Optional[str] = Field(
        default=None, description="Date of birth: YYYY-MM-DD, YYYY-MM, or YYYY"
    )
    nationalities: List[str] = Field(
        default_factory=list, description="ISO 3166-1 alpha-2 nationality codes"
    )
    addresses: List[str] = Field(default_factory=list, description="Normalised addresses")
    id_numbers: List[str] = Field(
        default_factory=list, description="Passport, national ID, etc. (normalised)"
    )
    countries: List[str] = Field(
        default_factory=list,
        description="Associated countries (from addresses / nationalities)",
    )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_screening_input(
        cls,
        name_en: str,
        name_he: Optional[str] = None,
        dob: Optional[str] = None,
        nationalities: Optional[List[str]] = None,
    ) -> "EntityProfile":
        """Create an :class:`EntityProfile` from the analyst screening form.

        Parameters
        ----------
        name_en:
            Primary English name of the subject.
        name_he:
            Optional Hebrew name (stored as an alias).
        dob:
            Optional date of birth (any reasonable format).
        nationalities:
            Optional list of ISO-3166-1 alpha-2 nationality codes.

        Returns
        -------
        EntityProfile
            A fully normalised profile ready for scoring.
        """
        aliases: List[str] = []
        if name_he:
            aliases.append(name_he)

        profile = cls(
            name=name_en,
            aliases=aliases,
            date_of_birth=dob,
            nationalities=nationalities or [],
        )
        return profile.normalise()

    @classmethod
    def from_sanctions_record(cls, record: Dict[str, Any]) -> "EntityProfile":
        """Create an :class:`EntityProfile` from a raw sanctions / screening API record.

        The *record* dict is expected to contain keys such as ``name``,
        ``aliases``, ``dateOfBirth``, ``nationalities``, ``addresses``,
        ``idNumbers``, ``countries``, etc.  Non-standard keys are ignored.

        Parameters
        ----------
        record:
            Raw dictionary returned by the upstream screening API.

        Returns
        -------
        EntityProfile
            A fully normalised profile ready for scoring.
        """
        # Attempt multiple common key variants for robustness
        name = record.get("name") or record.get("fullName") or record.get("entity_name") or ""

        aliases: List[str] = []
        for key in ("aliases", "alsoKnownAs", "akas", "alt_names"):
            val = record.get(key)
            if val:
                if isinstance(val, list):
                    aliases.extend(val)
                elif isinstance(val, str):
                    aliases.append(val)

        dob = record.get("dateOfBirth") or record.get("dob") or record.get("date_of_birth")

        nationalities: List[str] = []
        for key in ("nationalities", "nationality", "citizenships"):
            val = record.get(key)
            if val:
                if isinstance(val, list):
                    nationalities.extend(val)
                elif isinstance(val, str):
                    nationalities.append(val)

        addresses: List[str] = []
        for key in ("addresses", "address", "residences"):
            val = record.get(key)
            if val:
                if isinstance(val, list):
                    addresses.extend(val)
                elif isinstance(val, str):
                    addresses.append(val)

        id_numbers: List[str] = []
        for key in ("idNumbers", "id_numbers", "passportNumbers", "identification", "ids"):
            val = record.get(key)
            if val:
                if isinstance(val, list):
                    id_numbers.extend(str(v) for v in val)
                elif isinstance(val, str):
                    id_numbers.append(val)

        countries: List[str] = []
        for key in ("countries", "country", "associatedCountries"):
            val = record.get(key)
            if val:
                if isinstance(val, list):
                    countries.extend(val)
                elif isinstance(val, str):
                    countries.append(val)

        profile = cls(
            name=name,
            aliases=aliases,
            date_of_birth=dob,
            nationalities=nationalities,
            addresses=addresses,
            id_numbers=id_numbers,
            countries=countries,
        )
        return profile.normalise()

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    def normalise(self) -> "EntityProfile":
        """Return a normalised copy: lower-case, strip diacritics, standardise dates.

        The original instance is left untouched (immutable-style operation).
        """
        norm_name = _normalise_text(self.name) or ""
        norm_aliases = [
            a for a in (_normalise_text(alias) for alias in self.aliases) if a
        ]
        norm_dob = _normalise_date(self.date_of_birth)
        norm_nationalities = [
            nat.upper().strip() for nat in self.nationalities if nat.strip()
        ]
        norm_addresses = [
            addr for addr in (_normalise_text(a) for a in self.addresses) if addr
        ]
        norm_ids = [
            id_val.upper().strip().replace(" ", "")
            for id_val in self.id_numbers
            if id_val.strip()
        ]
        norm_countries = [
            c.upper().strip() for c in self.countries if c.strip()
        ]

        # Merge nationalities into countries if not already present
        merged_countries = list(dict.fromkeys(norm_countries + norm_nationalities))

        return EntityProfile(
            name=norm_name,
            aliases=norm_aliases,
            date_of_birth=norm_dob,
            nationalities=norm_nationalities,
            addresses=norm_addresses,
            id_numbers=norm_ids,
            countries=merged_countries,
        )


# ---------------------------------------------------------------------------
# AttributeMatcher
# ---------------------------------------------------------------------------

class AttributeMatcher:
    """Static helpers that score the similarity between individual attributes.

    Each ``match_*`` method returns a float in the range **0.0 – 1.0**,
    where **1.0** means perfect agreement and **0.0** means no agreement.
    """

    # Minimum Levenshtein ratio to consider two names as potentially related.
    # Below this threshold the score is clamped to 0.0.
    LEVENSHTEIN_THRESHOLD: float = 0.50

    @staticmethod
    def levenshtein_ratio(s1: str, s2: str) -> float:
        """Return the similarity ratio between *s1* and *s2* using
        :class:`difflib.SequenceMatcher`.

        The ratio is always in the range **0.0 – 1.0**.

        Examples
        --------
        >>> AttributeMatcher.levenshtein_ratio("john", "jon")
        0.75
        >>> AttributeMatcher.levenshtein_ratio("exact", "exact")
        1.0
        """
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0
        return SequenceMatcher(None, s1, s2).ratio()

    @staticmethod
    def match_name(name1: str, name2: str) -> float:
        """Score name similarity in the range **0.0 – 1.0**.

        Hierarchy
        ---------
        1. **Exact match**            → ``1.0``
        2. **Alias match**            → ``0.8``
        3. **Levenshtein similarity** → ``0.0 – 0.8`` (linearly scaled)
        """
        n1 = _normalise_text(name1) or ""
        n2 = _normalise_text(name2) or ""

        if not n1 or not n2:
            return 0.0

        # 1. Exact match
        if n1 == n2:
            return 1.0

        # 2. Token-order-independent exact match
        t1 = tuple(sorted(n1.split()))
        t2 = tuple(sorted(n2.split()))
        if t1 == t2:
            return 1.0

        # 3. Levenshtein ratio (scaled to 0.0-0.8)
        ratio = SequenceMatcher(None, n1, n2).ratio()
        if ratio < AttributeMatcher.LEVENSHTEIN_THRESHOLD:
            return 0.0
        return ratio * 0.8

    @staticmethod
    def match_name_with_aliases(
        name1: str, aliases1: List[str], name2: str, aliases2: List[str]
    ) -> float:
        """Score name similarity allowing cross-name / alias matching.

        Returns the **maximum** score among all pairwise combinations of
        primary names and aliases.

        Returns
        -------
        float
            Best similarity score found (0.0 – 1.0).
        """
        all_names_1 = [name1] + aliases1
        all_names_2 = [name2] + aliases2

        best = 0.0
        for a in all_names_1:
            for b in all_names_2:
                score = AttributeMatcher.match_name(a, b)
                if score > best:
                    best = score
                if best == 1.0:
                    return 1.0
        return best

    @staticmethod
    def match_dob(dob1: Optional[str], dob2: Optional[str]) -> float:
        """Score date-of-birth similarity in the range **0.0 – 1.0**.

        Scoring rules
        -------------
        ==============  =====
        Match type      Score
        ==============  =====
        Exact full date  1.0
        Year + month     0.6
        Year only        0.3
        No overlap       0.0
        ==============  =====
        """
        nd1 = _normalise_date(dob1)
        nd2 = _normalise_date(dob2)

        if nd1 is None or nd2 is None:
            return 0.0

        # Exact match
        if nd1 == nd2:
            # Full YYYY-MM-DD
            if len(nd1) == 10:
                return 1.0
            # YYYY-MM
            if len(nd1) == 7:
                return 0.6
            # YYYY
            return 0.3

        y1, m1 = _extract_year_month(nd1)
        y2, m2 = _extract_year_month(nd2)

        # Year mismatch → no match at all
        if y1 != y2 or y1 is None:
            return 0.0

        # Same year, check month
        if m1 is not None and m2 is not None:
            if m1 == m2:
                return 0.6  # Same year + month, different day (or no day)
            return 0.0  # Same year, different month

        # One side has year only
        return 0.3

    @staticmethod
    def match_nationality(nat1: List[str], nat2: List[str]) -> float:
        """Score nationality overlap using Jaccard index (**0.0 – 1.0**).

        Nationalities are expected to be ISO-3166-1 alpha-2 codes
        (e.g. ``"US"``, ``"IL"``).
        """
        return _jaccard_index(nat1, nat2)

    @staticmethod
    def match_country(country1: List[str], country2: List[str]) -> float:
        """Score country overlap using Jaccard index (**0.0 – 1.0**).

        Countries are expected to be ISO-3166-1 alpha-2 codes.
        """
        return _jaccard_index(country1, country2)

    @staticmethod
    def match_id_number(ids1: List[str], ids2: List[str]) -> float:
        """Score ID number overlap (**0.0 – 1.0**).

        An exact match on any ID number pair yields ``1.0`` — this is the
        strongest possible resolution signal.
        """
        set1: Set[str] = {id_val.upper().strip().replace(" ", "") for id_val in ids1 if id_val.strip()}
        set2: Set[str] = {id_val.upper().strip().replace(" ", "") for id_val in ids2 if id_val.strip()}

        if not set1 or not set2:
            return 0.0

        if set1 & set2:
            return 1.0
        return 0.0


# ---------------------------------------------------------------------------
# ResolutionScorer
# ---------------------------------------------------------------------------

class ResolutionScorer:
    """Multi-attribute weighted scoring engine for entity resolution.

    Computes an overall resolution score by taking a weighted average of
    individual attribute scores.  When one or both profiles lack a given
    attribute the weights are **automatically re-normalised** so the score
    remains meaningful.

    Parameters
    ----------
    weights:
        Optional dictionary overriding the default attribute weights.
        Must contain the keys ``name``, ``dob``, ``nationality``,
        ``country``, ``id_number``.
    """

    # Default weights — tuned for sanctions-list screening; sum to 1.0.
    DEFAULT_WEIGHTS: Dict[str, float] = {
        "name": 0.35,
        "dob": 0.25,
        "nationality": 0.15,
        "country": 0.15,
        "id_number": 0.10,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None) -> None:
        self.weights: Dict[str, float] = weights or self.DEFAULT_WEIGHTS.copy()
        self._matcher = AttributeMatcher()

    # ------------------------------------------------------------------
    # Core scoring
    # ------------------------------------------------------------------

    def score(self, subject: EntityProfile, record: EntityProfile) -> float:
        """Calculate the overall resolution score (**0.0 – 1.0**).

        The score is a weighted average of individual attribute scores.
        Weights are automatically re-normalised when attributes are missing
        from **both** profiles (i.e. no information to compare).

        Parameters
        ----------
        subject:
            The entity profile built from the screening form.
        record:
            The candidate record returned by the screening API.

        Returns
        -------
        float
            Resolution score in the range ``0.0`` to ``1.0``.
        """
        attr_scores, active_weights = self._compute_raw_scores(subject, record)
        weight_sum = sum(active_weights.values())
        if weight_sum == 0:
            return 0.0

        weighted = sum(
            attr_scores[attr] * (active_weights[attr] / weight_sum)
            for attr in active_weights
        )
        # Clamp to [0, 1] to guard against floating-point drift
        return max(0.0, min(1.0, weighted))

    def _compute_raw_scores(
        self, subject: EntityProfile, record: EntityProfile
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Return raw per-attribute scores and the active weight map.

        An attribute is *inactive* (weight dropped) when **both** sides
        lack any data for it, because there is nothing to compare.
        """
        attr_scores: Dict[str, float] = {}
        active_weights: Dict[str, float] = {}

        # ---- name ----
        name_score = self._matcher.match_name_with_aliases(
            subject.name, subject.aliases, record.name, record.aliases
        )
        attr_scores["name"] = name_score
        active_weights["name"] = self.weights["name"]

        # ---- dob ----
        has_dob = subject.date_of_birth is not None or record.date_of_birth is not None
        dob_score = self._matcher.match_dob(subject.date_of_birth, record.date_of_birth)
        attr_scores["dob"] = dob_score
        if has_dob:
            active_weights["dob"] = self.weights["dob"]

        # ---- nationality ----
        has_nat = bool(subject.nationalities or record.nationalities)
        nat_score = self._matcher.match_nationality(subject.nationalities, record.nationalities)
        attr_scores["nationality"] = nat_score
        if has_nat:
            active_weights["nationality"] = self.weights["nationality"]

        # ---- country ----
        has_country = bool(subject.countries or record.countries)
        country_score = self._matcher.match_country(subject.countries, record.countries)
        attr_scores["country"] = country_score
        if has_country:
            active_weights["country"] = self.weights["country"]

        # ---- id_number ----
        has_id = bool(subject.id_numbers or record.id_numbers)
        id_score = self._matcher.match_id_number(subject.id_numbers, record.id_numbers)
        attr_scores["id_number"] = id_score
        if has_id:
            active_weights["id_number"] = self.weights["id_number"]

        return attr_scores, active_weights

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    @staticmethod
    def classify(score: float) -> str:
        """Classify match strength based on the resolution score.

        ============  =================  ====================================
        Score range   Classification     Analyst action
        ============  =================  ====================================
        ≥ 0.85        ``strong_match``   Likely true positive, auto-flag
        0.50 – 0.84   ``moderate_match`` Standard triage queue
        0.20 – 0.49   ``weak_match``     Low priority, may auto-dismiss
        < 0.20        ``no_match``       Filtered out, not shown
        ============  =================  ====================================
        """
        if score >= 0.85:
            return "strong_match"
        if score >= 0.50:
            return "moderate_match"
        if score >= 0.20:
            return "weak_match"
        return "no_match"

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------

    def explain(
        self, subject: EntityProfile, record: EntityProfile
    ) -> Dict[str, Any]:
        """Return a detailed breakdown of how the score was calculated.

        The returned dictionary contains:

        * ``score`` — overall resolution score.
        * ``classification`` — match strength label.
        * ``attribute_breakdown`` — per-attribute scores, weights, and
          human-readable notes.
        * ``explanation`` — a single human-readable paragraph summarising
          the entire result.

        This is useful for analyst review, audit trails, and Red-Team
        analysis (e.g. checking why a false positive received a high score).
        """
        attr_scores, active_weights = self._compute_raw_scores(subject, record)
        weight_sum = sum(active_weights.values())
        overall = 0.0
        if weight_sum > 0:
            overall = sum(
                attr_scores[attr] * (active_weights[attr] / weight_sum)
                for attr in active_weights
            )
        overall = max(0.0, min(1.0, overall))
        classification = self.classify(overall)

        breakdown: Dict[str, Dict[str, Any]] = {}
        detail_lines: List[str] = []

        # --- name ---
        name_note = self._explain_name(subject, record, attr_scores["name"])
        breakdown["name"] = {
            "score": round(attr_scores["name"], 4),
            "weight": self.weights["name"],
            "active": "name" in active_weights,
            "note": name_note,
        }
        if "name" in active_weights:
            effective_w = self.weights["name"] / weight_sum
            detail_lines.append(
                f"Name: '{subject.name}' vs '{record.name}' — {name_note} "
                f"(effective weight {effective_w:.2f})"
            )

        # --- dob ---
        dob_note = self._explain_dob(subject, record, attr_scores["dob"])
        breakdown["dob"] = {
            "score": round(attr_scores["dob"], 4),
            "weight": self.weights["dob"],
            "active": "dob" in active_weights,
            "note": dob_note,
        }
        if "dob" in active_weights:
            effective_w = self.weights["dob"] / weight_sum
            detail_lines.append(
                f"DOB: {subject.date_of_birth or 'N/A'} vs {record.date_of_birth or 'N/A'} — "
                f"{dob_note} (effective weight {effective_w:.2f})"
            )

        # --- nationality ---
        nat_note = self._explain_nationality(subject, record, attr_scores["nationality"])
        breakdown["nationality"] = {
            "score": round(attr_scores["nationality"], 4),
            "weight": self.weights["nationality"],
            "active": "nationality" in active_weights,
            "note": nat_note,
        }
        if "nationality" in active_weights:
            effective_w = self.weights["nationality"] / weight_sum
            detail_lines.append(
                f"Nationality: {subject.nationalities or 'N/A'} vs {record.nationalities or 'N/A'} — "
                f"{nat_note} (effective weight {effective_w:.2f})"
            )

        # --- country ---
        country_note = self._explain_country(subject, record, attr_scores["country"])
        breakdown["country"] = {
            "score": round(attr_scores["country"], 4),
            "weight": self.weights["country"],
            "active": "country" in active_weights,
            "note": country_note,
        }
        if "country" in active_weights:
            effective_w = self.weights["country"] / weight_sum
            detail_lines.append(
                f"Country: {subject.countries or 'N/A'} vs {record.countries or 'N/A'} — "
                f"{country_note} (effective weight {effective_w:.2f})"
            )

        # --- id_number ---
        id_note = self._explain_id_number(subject, record, attr_scores["id_number"])
        breakdown["id_number"] = {
            "score": round(attr_scores["id_number"], 4),
            "weight": self.weights["id_number"],
            "active": "id_number" in active_weights,
            "note": id_note,
        }
        if "id_number" in active_weights:
            effective_w = self.weights["id_number"] / weight_sum
            detail_lines.append(
                f"ID numbers: {subject.id_numbers or 'N/A'} vs {record.id_numbers or 'N/A'} — "
                f"{id_note} (effective weight {effective_w:.2f})"
            )

        explanation = "\n".join(detail_lines)
        explanation += f"\nOverall score: {overall:.4f} ({classification})"

        return {
            "score": round(overall, 4),
            "classification": classification,
            "attribute_breakdown": breakdown,
            "explanation": explanation,
        }

    # ------------------------------------------------------------------
    # Internal explain helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _explain_name(subject: EntityProfile, record: EntityProfile, score: float) -> str:
        """Generate a human-readable note for the name match."""
        if score == 1.0:
            return "exact match"
        if score == 0.8:
            return "alias match"
        if score > 0:
            return f"Levenshtein similarity {score:.2f}"
        return "no match"

    @staticmethod
    def _explain_dob(
        subject: EntityProfile, record: EntityProfile, score: float
    ) -> str:
        """Generate a human-readable note for the DOB match."""
        if score == 1.0:
            return "exact match"
        if score == 0.6:
            return "year+month match"
        if score == 0.3:
            return "year only match"
        if score == 0.0 and (subject.date_of_birth or record.date_of_birth):
            return "mismatch"
        return "not available"

    @staticmethod
    def _explain_nationality(
        subject: EntityProfile, record: EntityProfile, score: float
    ) -> str:
        """Generate a human-readable note for the nationality match."""
        if score == 1.0:
            return "exact match"
        if score > 0:
            return f"Jaccard overlap {score:.2f}"
        if subject.nationalities or record.nationalities:
            return "no overlap"
        return "not available"

    @staticmethod
    def _explain_country(
        subject: EntityProfile, record: EntityProfile, score: float
    ) -> str:
        """Generate a human-readable note for the country match."""
        if score == 1.0:
            return "exact match"
        if score > 0:
            return f"Jaccard overlap {score:.2f}"
        if subject.countries or record.countries:
            return "no overlap"
        return "not available"

    @staticmethod
    def _explain_id_number(
        subject: EntityProfile, record: EntityProfile, score: float
    ) -> str:
        """Generate a human-readable note for the ID number match."""
        if score == 1.0:
            return "exact match on at least one ID"
        if subject.id_numbers or record.id_numbers:
            return "no match"
        return "not available"


# ---------------------------------------------------------------------------
# ResolutionResult
# ---------------------------------------------------------------------------

class ResolutionResult(BaseModel):
    """Structured result of entity resolution for a single matched record.

    This model is the primary output of the resolution pipeline and is
    serialised directly into the screening report JSON / database row.
    """

    score: float = Field(
        ..., ge=0.0, le=1.0, description="Overall resolution score (0.0 – 1.0)"
    )
    classification: str = Field(
        ...,
        description="One of: strong_match, moderate_match, weak_match, no_match",
    )
    subject_profile: EntityProfile = Field(
        ..., description="The subject profile used for scoring"
    )
    record_profile: EntityProfile = Field(
        ..., description="The candidate record profile that was scored"
    )
    attribute_breakdown: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-attribute scores, weights, and human-readable notes",
    )
    explanation: str = Field(
        default="",
        description="Human-readable multi-line explanation of the score",
    )


# ---------------------------------------------------------------------------
# ResolutionBatchProcessor
# ---------------------------------------------------------------------------

class ResolutionBatchProcessor:
    """Process multiple candidate matches against a single subject.

    The processor scores every candidate, filters out entries that fall
    below the ``no_match`` threshold, and returns the remainder ranked
    by score descending.
    """

    # Scores below this cutoff are classified as ``no_match`` and discarded.
    CUTOFF: float = 0.20

    def __init__(self, scorer: Optional[ResolutionScorer] = None) -> None:
        self.scorer: ResolutionScorer = scorer or ResolutionScorer()

    async def process_matches(
        self,
        subject: EntityProfile,
        matches: List[Dict[str, Any]],
    ) -> List[ResolutionResult]:
        """Score all matches, filter out ``no_match``, sort by score descending.

        Parameters
        ----------
        subject:
            The normalised profile of the subject being screened.
        matches:
            Raw record dictionaries returned by the upstream screening API.

        Returns
        -------
        List[ResolutionResult]
            Only matches with score ≥ ``0.20`` (i.e. not ``no_match``),
            ordered from highest to lowest score.
        """
        # Build record profiles concurrently (I/O-bound normalisation)
        loop = asyncio.get_running_loop()
        record_profiles: List[EntityProfile] = await asyncio.gather(
            *[
                loop.run_in_executor(None, EntityProfile.from_sanctions_record, m)
                for m in matches
            ]
        )

        # Run scoring concurrently using the default executor
        async def _score_one(
            record: EntityProfile,
        ) -> Optional[ResolutionResult]:
            expl = await loop.run_in_executor(
                None, self.scorer.explain, subject, record
            )
            score = expl["score"]
            classification = expl["classification"]
            if score < self.CUTOFF:
                return None
            return ResolutionResult(
                score=score,
                classification=classification,
                subject_profile=subject,
                record_profile=record,
                attribute_breakdown=expl["attribute_breakdown"],
                explanation=expl["explanation"],
            )

        maybe_results = await asyncio.gather(
            *[_score_one(rec) for rec in record_profiles]
        )

        results: List[ResolutionResult] = [
            r for r in maybe_results if r is not None
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def generate_report_section(self, results: List[ResolutionResult]) -> str:
        """Generate a Markdown section suitable for inclusion in a screening report.

        Parameters
        ----------
        results:
            The ranked list returned by :py:meth:`process_matches`.

        Returns
        -------
        str
            Markdown-formatted report section.
        """
        if not results:
            return "### Entity Resolution Results\n\nNo matches found above threshold.\n"

        lines: List[str] = [
            "### Entity Resolution Results",
            "",
            f"**Total matches above threshold:** {len(results)}",
            "",
            "| Rank | Record Name | Score | Classification |",
            "|------|-------------|-------|----------------|",
        ]

        for idx, res in enumerate(results, start=1):
            badge = {
                "strong_match": "🟢",
                "moderate_match": "🟡",
                "weak_match": "🟠",
                "no_match": "⚪",
            }.get(res.classification, "")
            lines.append(
                f"| {idx} | {res.record_profile.name} | "
                f"{res.score:.4f} | {badge} {res.classification} |"
            )

        lines.extend(["", "#### Detailed Breakdowns", ""])
        for idx, res in enumerate(results, start=1):
            lines.append(
                f"**{idx}. {res.record_profile.name}** — "
                f"score {res.score:.4f} ({res.classification})"
            )
            lines.append("```")
            lines.append(res.explanation)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience free-function API
# ---------------------------------------------------------------------------

async def resolve_matches(
    subject: EntityProfile,
    matches: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[ResolutionResult]:
    """One-shot async helper: score and rank all candidate matches.

    Parameters
    ----------
    subject:
        The subject profile.
    matches:
        Raw candidate records from the screening API.
    weights:
        Optional custom attribute weights.

    Returns
    -------
    List[ResolutionResult]
        Ranked, filtered results.
    """
    scorer = ResolutionScorer(weights=weights)
    processor = ResolutionBatchProcessor(scorer=scorer)
    return await processor.process_matches(subject, matches)
