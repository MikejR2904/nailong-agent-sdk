# Copyright (c) 2026 David Michael Indraputra

"""Evidence profile: write and check a run's independent evidence from one settings file.

* ``settings.py`` - the settings models and loading them from a file.
* ``build.py`` - turning settings into keys, sink, recorder clients, authority and roots.
* ``checks.py`` - the checks run on a run, one function each, listed in ``CHECKS``.
* ``profile.py`` - ``EvidenceProfile``: the checkpointer, the recorder mirror and ``verify``.
"""

from __future__ import annotations

from .checks import CHECKS as CHECKS
from .checks import EvidenceContext as EvidenceContext
from .checks import EvidenceFinding as EvidenceFinding
from .checks import EvidenceReport as EvidenceReport
from .profile import EvidenceProfile as EvidenceProfile
from .settings import PROFILE_SCHEMA as PROFILE_SCHEMA
from .settings import EvidenceSettings as EvidenceSettings
from .settings import RecorderSettings as RecorderSettings
from .settings import WitnessSettings as WitnessSettings

__all__ = [
    "CHECKS",
    "PROFILE_SCHEMA",
    "EvidenceContext",
    "EvidenceFinding",
    "EvidenceProfile",
    "EvidenceReport",
    "EvidenceSettings",
    "RecorderSettings",
    "WitnessSettings",
]
