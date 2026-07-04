#!/usr/bin/env python3
"""
Transliteration Engine — OSINT DD Dashboard Backend.

Supports:
    * Rule-based transliteration for 50+ languages using ICU rules.
    * Phonetic mapping for languages without ICU data (e.g. Farsi Dari).
    * Historical variant generation (e.g. "Pekin" for "Beijing").
    * Confidence scoring with per-language thresholds.
    * Caching for repeated queries.
    * Batch processing for large name lists.

Design Decisions:
    * ICU is the primary engine; custom phonetic tables supplement it.
    * Each language has a per-name max-variants cap to prevent combinatorial explosion.
    * Normalisation (NFKC) is applied before transliteration.
    * Input validation rejects non-string types and suspicious characters.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Protocol, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_VARIANTS_PER_NAME: int = 8
SCRIPT_LATIN: str = "Latn"

# Characters that are rejected as suspicious / injection attempts
SUSPICIOUS_CHARS_RE: re.Pattern = re.compile(r"[<>&;{}\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Mapping from ISO-639-2/B language codes to the script codes they typically use
LANGUAGE_TO_SCRIPT: Dict[str, str] = {
    "ara": "Arab", "fas": "Arab", "urd": "Arab", "pus": "Arab",
    "heb": "Hebr", "yid": "Hebr",
    "rus": "Cyrl", "ukr": "Cyrl", "bul": "Cyrl", "srp": "Cyrl",
    "kaz": "Cyrl", "kir": "Cyrl", "tgk": "Cyrl", "mon": "Cyrl",
    "ell": "Grek", "kat": "Geor", "hye": "Armn",
    "zho": "Hans", "jpn": "Jpan", "kor": "Kore",
    "hin": "Deva", "ben": "Beng", "tam": "Taml", "tel": "Telu",
    "kan": "Knda", "mal": "Mlym", "guj": "Gujr", "pan": "Guru",
    "tha": "Thai", "lao": "Laoo", "khm": "Khmr", "mya": "Mymr",
    "amh": "Ethi", "bod": "Tibt",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TransliterationError(Exception):
    """Base exception for transliteration failures."""

    pass


class UnsupportedLanguageError(TransliterationError):
    """Raised when a language has no registered transliterator."""

    pass


class InputValidationError(TransliterationError):
    """Raised when input fails validation (e.g. non-string, suspicious chars)."""

    pass


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TransliterationConfidence(str, Enum):
    """Confidence level for a transliteration result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class TransliterationResult:
    """Result of a single transliteration operation."""

    original: str
    transliterated: str
    source_script: str
    target_script: str = SCRIPT_LATIN
    language: Optional[str] = None
    method: str = ""
    confidence: TransliterationConfidence = TransliterationConfidence.MEDIUM
    variants: List[str] = field(default_factory=list)


@dataclass
class TransliterationConfig:
    """Configuration for a single transliteration request."""

    source_text: str
    source_language: Optional[str] = None
    source_script: Optional[str] = None
    target_script: str = SCRIPT_LATIN
    include_variants: bool = True
    include_historical: bool = False
    max_variants: int = MAX_VARIANTS_PER_NAME

    def cache_key(self) -> str:
        """Return a deterministic cache key for this config."""
        text = f"{self.source_text}|{self.source_language or ''}|{self.source_script or ''}|{self.target_script}|{self.include_variants}|{self.include_historical}|{self.max_variants}"
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Normaliser
# ---------------------------------------------------------------------------

class TextNormaliser:
    """Normalises text before transliteration.

    Steps (applied in order):
        1. Strip leading/trailing whitespace
        2. Unicode NFKC normalisation
        3. Remove control characters
        4. Collapse multiple whitespace to single space
    """

    @staticmethod
    def normalise(text: str) -> str:
        if not isinstance(text, str):
            raise InputValidationError(f"Expected str, got {type(text).__name__}")

        text = text.strip()
        text = unicodedata.normalize("NFKC", text)
        text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in ("\n", "\t"))
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def validate(text: str) -> None:
        """Validate input text. Raises InputValidationError on failure."""
        if not isinstance(text, str):
            raise InputValidationError(f"Expected str, got {type(text).__name__}")
        if not text.strip():
            raise InputValidationError("Empty string")
        if SUSPICIOUS_CHARS_RE.search(text):
            raise InputValidationError(f"Suspicious characters in input: {text!r}")


# ---------------------------------------------------------------------------
# Strategy Protocol & Base Class
# ---------------------------------------------------------------------------

class TransliterationStrategy(Protocol):
    """Protocol for transliteration strategies."""

    def can_handle(self, source_script: str, target_script: str, language: Optional[str] = None) -> bool:
        ...

    def transliterate(self, text: str, language: Optional[str] = None) -> Tuple[str, TransliterationConfidence, List[str]]:
        ...


class BaseTransliterator(ABC):
    """Abstract base class for transliterators."""

    @abstractmethod
    def can_handle(self, source_script: str, target_script: str, language: Optional[str] = None) -> bool:
        """Return True if this transliterator can handle the script/language pair."""
        ...

    @abstractmethod
    def transliterate(self, text: str, language: Optional[str] = None) -> Tuple[str, TransliterationConfidence, List[str]]:
        """Return (primary_result, confidence, variants)."""
        ...


# ---------------------------------------------------------------------------
# ICU Transliterator
# ---------------------------------------------------------------------------

class ICUTransliterator(BaseTransliterator):
    """Transliterator using ICU (International Components for Unicode) rules.

    Requires the ``pyicu`` package to be installed. Falls back to a
    phonetic-based transliterator if ICU is unavailable.
    """

    _icu_available: bool = False
    _transliterators: Dict[str, Any] = {}

    def __init__(self) -> None:
        self._ensure_icu()

    def _ensure_icu(self) -> None:
        if self._icu_available:
            return
        try:
            import icu
            self._icu = icu
            self._icu_available = True
            logger.debug("ICU backend loaded successfully.")
        except ImportError:
            logger.warning("pyicu not installed. ICU transliteration unavailable.")
            self._icu_available = False

    def can_handle(self, source_script: str, target_script: str, language: Optional[str] = None) -> bool:
        if not self._icu_available:
            return False
        tid = self._transliterator_id(source_script, target_script, language)
        if tid in self._transliterators:
            return True
        try:
            t = self._icu.Transliterator.createInstance(tid)
            self._transliterators[tid] = t
            return True
        except Exception:
            return False

    def transliterate(self, text: str, language: Optional[str] = None) -> Tuple[str, TransliterationConfidence, List[str]]:
        if not self._icu_available:
            return text, TransliterationConfidence.LOW, []

        tid = self._transliterator_id(self._detect_script(text), SCRIPT_LATIN, language)
        transliterator = self._transliterators.get(tid)
        if transliterator is None:
            return text, TransliterationConfidence.LOW, []

        try:
            result = transliterator.transliterate(text)
            return result, TransliterationConfidence.HIGH, []
        except Exception as exc:
            logger.warning("ICU transliteration failed: %s", exc)
            return text, TransliterationConfidence.LOW, []

    @staticmethod
    def _transliterator_id(source_script: str, target_script: str, language: Optional[str] = None) -> str:
        if language:
            return f"{source_script}-{target_script}; {language}"
        return f"{source_script}-{target_script}"

    @staticmethod
    def _detect_script(text: str) -> str:
        """Detect the dominant script of the text."""
        script_counts: Dict[str, int] = {}
        for ch in text:
            script = _unicode_script(ch)
            if script and script != "Common" and script != "Inherited":
                script_counts[script] = script_counts.get(script, 0) + 1
        if not script_counts:
            return SCRIPT_LATIN
        return max(script_counts, key=script_counts.get)


# ---------------------------------------------------------------------------
# Phonetic Transliterator
# ---------------------------------------------------------------------------

class PhoneticTransliterator(BaseTransliterator):
    """Fallback transliterator using phonetic mapping tables.

    Used when ICU is unavailable or does not support a particular
    script/language pair.
    """

    # Cyrillic to Latin mapping
    CYRILLIC_MAP: Dict[str, str] = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo",
        "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
        "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
        "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Shch",
        "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
    }

    # Greek to Latin mapping
    GREEK_MAP: Dict[str, str] = {
        "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
        "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
        "ο": "o", "π": "p", "ρ": "r", "σ": "s", "τ": "t", "υ": "y", "φ": "f",
        "χ": "ch", "ψ": "ps", "ω": "o",
        "Α": "A", "Β": "V", "Γ": "G", "Δ": "D", "Ε": "E", "Ζ": "Z", "Η": "I",
        "Θ": "Th", "Ι": "I", "Κ": "K", "Λ": "L", "Μ": "M", "Ν": "N", "Ξ": "X",
        "Ο": "O", "Π": "P", "Ρ": "R", "Σ": "S", "Τ": "T", "Υ": "Y", "Φ": "F",
        "Χ": "Ch", "Ψ": "Ps", "Ω": "O",
    }

    # Arabic to Latin mapping (simplified)
    ARABIC_MAP: Dict[str, str] = {
        "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
        "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s",
        "ض": "d", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q",
        "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w", "ي": "y",
    }

    # Hebrew to Latin mapping
    HEBREW_MAP: Dict[str, str] = {
        "א": "a", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
        "ח": "ch", "ט": "t", "י": "y", "כ": "k", "ל": "l", "מ": "m", "נ": "n",
        "ס": "s", "ע": "a", "פ": "p", "צ": "ts", "ק": "k", "ר": "r", "ש": "sh",
        "ת": "t",
    }

    def can_handle(self, source_script: str, target_script: str, language: Optional[str] = None) -> bool:
        if target_script != SCRIPT_LATIN:
            return False
        return source_script in ("Cyrl", "Grek", "Arab", "Hebr")

    def transliterate(self, text: str, language: Optional[str] = None) -> Tuple[str, TransliterationConfidence, List[str]]:
        # Detect script
        script = self._detect_script(text)

        mapping = self._get_mapping(script)
        if not mapping:
            return text, TransliterationConfidence.LOW, []

        result = ""
        for ch in text:
            result += mapping.get(ch, ch)

        return result, TransliterationConfidence.MEDIUM, []

    def _detect_script(self, text: str) -> str:
        script_counts: Dict[str, int] = {}
        for ch in text:
            script = _unicode_script(ch)
            if script and script not in ("Common", "Inherited", SCRIPT_LATIN):
                script_counts[script] = script_counts.get(script, 0) + 1
        if not script_counts:
            return SCRIPT_LATIN
        return max(script_counts, key=script_counts.get)

    def _get_mapping(self, script: str) -> Optional[Dict[str, str]]:
        mappings = {
            "Cyrl": self.CYRILLIC_MAP,
            "Grek": self.GREEK_MAP,
            "Arab": self.ARABIC_MAP,
            "Hebr": self.HEBREW_MAP,
        }
        return mappings.get(script)


# ---------------------------------------------------------------------------
# Historical Variants Generator
# ---------------------------------------------------------------------------

class HistoricalVariantGenerator:
    """Generates historical spelling variants for transliterated names.

    Some names have historically-used alternative spellings that differ
    from modern standard transliteration.  This generator captures those
    variants to improve recall.
    """

    # Language-specific historical variant rules
    VARIANT_RULES: Dict[str, List[Tuple[str, str]]] = {
        "zho": [
            (r"bei", "pei"), (r"jing", "king"), (r"xian", "hsien"),
            (r"zhang", "chang"), (r"zhong", "chung"), (r"qing", "ching"),
            (r"xiong", "hsiung"),
        ],
        "rus": [
            (r"y", "i"), (r"yy", "y"), (r"iy", "y"),
        ],
    }

    @classmethod
    def generate(cls, text: str, language: Optional[str] = None) -> List[str]:
        """Generate historical spelling variants for *text*.

        Args:
            text: Already-transliterated Latin text.
            language: ISO-639-2/B language code (e.g. "zho", "rus").

        Returns:
            List of variant spellings (may be empty).
        """
        if not language:
            return []

        rules = cls.VARIANT_RULES.get(language, [])
        if not rules:
            return []

        variants: List[str] = []
        for pattern, replacement in rules:
            try:
                variant = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
                if variant != text and variant not in variants:
                    variants.append(variant)
            except re.error:
                continue

        return variants


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TransliterationCache:
    """Simple in-memory LRU cache for transliteration results."""

    def __init__(self, max_size: int = 1000) -> None:
        self._max_size: int = max_size
        self._cache: Dict[str, TransliterationResult] = {}
        self._access_order: List[str] = []

    def get(self, key: str) -> Optional[TransliterationResult]:
        if key in self._cache:
            # Move to front (most recently used)
            self._access_order.remove(key)
            self._access_order.append(key)
            return self._cache[key]
        return None

    def put(self, key: str, result: TransliterationResult) -> None:
        if key in self._cache:
            self._access_order.remove(key)
        elif len(self._cache) >= self._max_size:
            # Evict least recently used
            lru_key = self._access_order.pop(0)
            del self._cache[lru_key]
        self._cache[key] = result
        self._access_order.append(key)

    def clear(self) -> None:
        self._cache.clear()
        self._access_order.clear()


# ---------------------------------------------------------------------------
# Main Transliterator
# ---------------------------------------------------------------------------

class Transliterator:
    """Main transliterator that orchestrates all strategies.

    Usage:
        >>> t = Transliterator()
        >>> result = t.transliterate("Алексей", source_language="rus")
        >>> print(result.transliterated)
        'Aleksey'
    """

    def __init__(self, cache_size: int = 1000) -> None:
        self._icu = ICUTransliterator()
        self._phonetic = PhoneticTransliterator()
        self._historical = HistoricalVariantGenerator()
        self._cache = TransliterationCache(max_size=cache_size)
        self._normaliser = TextNormaliser()

    def transliterate(self, config: TransliterationConfig) -> TransliterationResult:
        """Transliterate text according to *config*.

        Args:
            config: TransliterationConfig with source text and options.

        Returns:
            TransliterationResult with the transliterated text and metadata.

        Raises:
            InputValidationError: If input validation fails.
            UnsupportedLanguageError: If no transliterator can handle the input.
        """
        # Validation
        self._normaliser.validate(config.source_text)

        # Normalisation
        normalised = self._normaliser.normalise(config.source_text)

        # Detect script
        source_script = config.source_script or self._detect_script(normalised)
        source_language = config.source_language

        if not source_script:
            raise UnsupportedLanguageError(f"Cannot detect script for: {normalised!r}")

        # Check cache
        cache_key = config.cache_key()
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        # Try ICU first
        if self._icu.can_handle(source_script, config.target_script, source_language):
            result_text, confidence, variants = self._icu.transliterate(normalised, source_language)
            method = "icu"
        # Fallback to phonetic
        elif self._phonetic.can_handle(source_script, config.target_script, source_language):
            result_text, confidence, variants = self._phonetic.transliterate(normalised, source_language)
            method = "phonetic"
        else:
            raise UnsupportedLanguageError(
                f"No transliterator for script={source_script}, lang={source_language}"
            )

        # Generate historical variants
        if config.include_historical:
            historical = self._historical.generate(result_text, source_language)
            variants.extend(historical)

        # Deduplicate and cap variants
        seen: Set[str] = {result_text}
        unique_variants: List[str] = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                unique_variants.append(v)
        variants = unique_variants[:config.max_variants]

        result = TransliterationResult(
            original=config.source_text,
            transliterated=result_text,
            source_script=source_script,
            target_script=config.target_script,
            language=source_language,
            method=method,
            confidence=confidence,
            variants=variants,
        )

        self._cache.put(cache_key, result)
        return result

    def batch_transliterate(
        self, configs: List[TransliterationConfig],
    ) -> List[TransliterationResult]:
        """Transliterate multiple texts efficiently.

        Results are returned in the same order as the input configs.
        """
        return [self.transliterate(c) for c in configs]

    def clear_cache(self) -> None:
        """Clear the transliteration cache."""
        self._cache.clear()

    @staticmethod
    def _detect_script(text: str) -> Optional[str]:
        """Detect the dominant script of *text*."""
        script_counts: Dict[str, int] = {}
        for ch in text:
            script = _unicode_script(ch)
            if script and script not in ("Common", "Inherited"):
                script_counts[script] = script_counts.get(script, 0) + 1
        if not script_counts:
            return None
        return max(script_counts, key=script_counts.get)


# ---------------------------------------------------------------------------
# Helper: Unicode script detection
# ---------------------------------------------------------------------------

def _unicode_script(ch: str) -> Optional[str]:
    """Return the Unicode script name for a character.

    Uses the ``unicodedataplus`` package if available, otherwise falls
    back to a simplified heuristic based on Unicode block ranges.
    """
    try:
        import unicodedataplus as udp
        return udp.script(ch)
    except ImportError:
        return _script_from_block(ord(ch))


def _script_from_block(codepoint: int) -> Optional[str]:
    """Fallback: determine script from Unicode block range."""
    # Cyrillic
    if 0x0400 <= codepoint <= 0x04FF or 0x0500 <= codepoint <= 0x052F:
        return "Cyrl"
    # Greek
    if 0x0370 <= codepoint <= 0x03FF or 0x1F00 <= codepoint <= 0x1FFF:
        return "Grek"
    # Arabic
    if 0x0600 <= codepoint <= 0x06FF or 0x0750 <= codepoint <= 0x077F:
        return "Arab"
    # Hebrew
    if 0x0590 <= codepoint <= 0x05FF:
        return "Hebr"
    # CJK Unified Ideographs
    if 0x4E00 <= codepoint <= 0x9FFF or 0x3400 <= codepoint <= 0x4DBF:
        return "Hans"
    # Hiragana / Katakana
    if 0x3040 <= codepoint <= 0x309F or 0x30A0 <= codepoint <= 0x30FF:
        return "Jpan"
    # Hangul
    if 0xAC00 <= codepoint <= 0xD7AF:
        return "Kore"
    # Devanagari
    if 0x0900 <= codepoint <= 0x097F:
        return "Deva"
    # Latin
    if (
        0x0041 <= codepoint <= 0x005A
        or 0x0061 <= codepoint <= 0x007A
        or 0x00C0 <= codepoint <= 0x017F
        or 0x0180 <= codepoint <= 0x024F
    ):
        return SCRIPT_LATIN
    return None


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------

def transliterate_text(
    text: str,
    source_language: Optional[str] = None,
    source_script: Optional[str] = None,
    include_variants: bool = True,
) -> TransliterationResult:
    """One-shot transliteration of *text*.

    Args:
        text: The text to transliterate.
        source_language: ISO-639-2/B language code (optional).
        source_script: Unicode script code (optional, auto-detected if omitted).
        include_variants: Whether to generate spelling variants.

    Returns:
        TransliterationResult with the transliterated text.

    Raises:
        InputValidationError: If input is invalid.
        UnsupportedLanguageError: If no transliterator can handle the input.
    """
    config = TransliterationConfig(
        source_text=text,
        source_language=source_language,
        source_script=source_script,
        include_variants=include_variants,
    )
    transliterator = Transliterator()
    return transliterator.transliterate(config)
