"""Public deep-module seam for assembling and revalidating worksets."""

from .workspace import assemble, hydrate

__all__ = [
    "assemble",
    "hydrate",
]
