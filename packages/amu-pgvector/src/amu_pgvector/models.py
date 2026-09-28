"""Small result types returned by AMUStore. Not the governance model itself
-- that's amu_governance.AMU/Lineage/LineageStep, reused as-is."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Conflict:
    """A pre-existing AMU for the same metric_name with a different
    definition_hash, owned by a different department. Carries no values or
    lineage -- amu.check_conflict() never returns them."""

    department: str
    definition_hash: str


@dataclass(frozen=True)
class RecordResult:
    amu_id: uuid.UUID
    lineage_status: str
    conflicts: list[Conflict]


@dataclass(frozen=True)
class SearchResult:
    id: uuid.UUID
    metric_name: str
    description: str
    value: dict
    owner_department: str
    definition_hash: str
    distance: float
