"""Jurisdiction-aware screening logic.

Exports all 8 jurisdiction adapters and a factory function.
"""

from __future__ import annotations

from typing import List

from .base import JurisdictionResult, JurisdictionSource
from .sources import (
    USOFACSource,
    UNSource,
    UKHMTSource,
    EUSource,
    IsraelSource,
    CanadaSEMASource,
    AustraliaDFATSource,
    FATFGreyListSource,
)

__all__ = [
    "JurisdictionResult",
    "JurisdictionSource",
    "USOFACSource",
    "UNSource",
    "UKHMTSource",
    "EUSource",
    "IsraelSource",
    "CanadaSEMASource",
    "AustraliaDFATSource",
    "FATFGreyListSource",
    "get_jurisdiction_sources",
]


def get_jurisdiction_sources() -> List[JurisdictionSource]:
    """Return all enabled jurisdiction source instances."""
    return [
        USOFACSource(),
        UNSource(),
        UKHMTSource(),
        EUSource(),
        IsraelSource(),
        CanadaSEMASource(),
        AustraliaDFATSource(),
        FATFGreyListSource(),
    ]
