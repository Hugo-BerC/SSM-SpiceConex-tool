from __future__ import annotations

from .models import InfrastructureGraph, ResourceEdge, ResourceNode


def edge(source: ResourceNode | str, target: ResourceNode | str, relation: str, confidence: str = "DIRECT", **metadata) -> ResourceEdge:
    source_id = source.id if isinstance(source, ResourceNode) else source
    target_id = target.id if isinstance(target, ResourceNode) else target
    return ResourceEdge(source_id, target_id, relation, confidence, metadata)


def add_relation(graph: InfrastructureGraph, source: str, target: str, relation: str, confidence: str = "DIRECT", **metadata) -> None:
    graph.add_edge(ResourceEdge(source, target, relation, confidence, metadata))
