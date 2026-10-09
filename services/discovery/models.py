from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResourceNode:
    id: str
    arn: str | None
    resource_type: str
    name: str | None
    region: str
    account_id: str
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def application(self) -> str | None:
        return self.tags.get("ib:resource:application")


@dataclass(frozen=True)
class ResourceEdge:
    source: str
    target: str
    relation: str
    confidence: str = "DIRECT"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InfrastructureGraph:
    account_id: str
    account_name: str
    regions: list[str] = field(default_factory=list)
    nodes: dict[str, ResourceNode] = field(default_factory=dict)
    edges: list[ResourceEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_node(self, node: ResourceNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: ResourceEdge) -> None:
        if edge.source in self.nodes and edge.target in self.nodes and edge.source != edge.target:
            if edge not in self.edges:
                self.edges.append(edge)

    def applications(self, tag_key: str = "ib:resource:application") -> list[str]:
        values = {
            node.tags.get(tag_key, "").strip()
            for node in self.nodes.values()
            if node.tags.get(tag_key, "").strip()
        }
        return sorted(values, key=str.casefold)

    def scoped(self, application: str | None, tag_key: str = "ib:resource:application", expand_shared: bool = True) -> "InfrastructureGraph":
        if not application or application.upper() == "ALL PRODUCTS":
            return self

        selected = {
            node_id for node_id, node in self.nodes.items()
            if node.tags.get(tag_key, "").strip().casefold() == application.casefold()
        }
        if expand_shared:
            # Include directly connected untagged/shared resources, but never
            # pull another tagged application's resources into the scope.
            changed = True
            while changed:
                changed = False
                for edge in self.edges:
                    if edge.source in selected and edge.target not in selected:
                        target = self.nodes.get(edge.target)
                        if target and not target.tags.get(tag_key, "").strip():
                            selected.add(edge.target); changed = True
                    elif edge.target in selected and edge.source not in selected:
                        source = self.nodes.get(edge.source)
                        if source and not source.tags.get(tag_key, "").strip():
                            selected.add(edge.source); changed = True

        result = InfrastructureGraph(
            account_id=self.account_id,
            account_name=self.account_name,
            regions=list(self.regions),
            warnings=list(self.warnings),
        )
        for node_id in selected:
            result.add_node(self.nodes[node_id])
        for edge in self.edges:
            if edge.source in selected and edge.target in selected:
                result.add_edge(edge)
        return result

    def resource_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.resource_type] = counts.get(node.resource_type, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
