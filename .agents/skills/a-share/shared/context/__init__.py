"""Public seams for assembling and hydrating A-share research worksets."""

from .source_payload import FileSourcePayloadStore, SourcePayloadStore
from .adapters import AugmentAdapter, SemanticAdapter
from .workspace import assemble, hydrate, persist_workset_manifest

__all__ = [
    "FileSourcePayloadStore",
    "SourcePayloadStore",
    "AugmentAdapter",
    "SemanticAdapter",
    "assemble",
    "hydrate",
    "persist_workset_manifest",
]
