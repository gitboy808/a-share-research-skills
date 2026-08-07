"""Optional semantic adapters kept behind the workset module boundary."""

from __future__ import annotations

from typing import Any, Callable


class SemanticAdapter:
    """Minimal adapter contract; implementations return candidate unit IDs."""

    name = "semantic"

    def search(self, query: str, limit: int = 10) -> list[Any]:  # pragma: no cover - interface
        raise NotImplementedError


class AugmentAdapter(SemanticAdapter):
    """An opt-in bridge for an installed Augment client.

    The constructor accepts a client or a search callable so the local path
    never imports or requires an external package.  Hits remain candidates;
    authority, coverage and hydration are still handled locally.
    """

    name = "augment"

    def __init__(self, client: Any = None, search_fn: Callable[..., Any] | None = None) -> None:
        self.client = client
        self.search_fn = search_fn

    @property
    def configured(self) -> bool:
        return self.client is not None or self.search_fn is not None

    def search(self, query: str, limit: int = 10) -> list[Any]:
        if not self.configured:
            return []
        if self.search_fn is not None:
            result = self.search_fn(query, limit=limit)
        elif hasattr(self.client, "search"):
            result = self.client.search(query, limit=limit)
        else:
            raise RuntimeError("configured Augment client has no search method")
        if isinstance(result, dict):
            return list(result.get("hits", result.get("results", [])))
        return list(result or [])
