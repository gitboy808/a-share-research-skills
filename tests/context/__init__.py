"""Public context seam tests.

``unittest discover`` imports this directory as the top-level name ``context``.
Load the production package under a private alias so the test package name
cannot shadow the public module seam.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_PRODUCTION = Path(__file__).resolve().parents[2] / ".agents/skills/a-share/shared/context"
_SPEC = importlib.util.spec_from_file_location(
    "a_share_context",
    _PRODUCTION / "__init__.py",
    submodule_search_locations=[str(_PRODUCTION)],
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("production context package not found")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("a_share_context", _MODULE)
_SPEC.loader.exec_module(_MODULE)

assemble = _MODULE.assemble
hydrate = _MODULE.hydrate
