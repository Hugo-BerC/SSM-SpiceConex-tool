from __future__ import annotations
from models.instance import EC2Instance

class InstanceService:
    @staticmethod
    def filter(instances: list[EC2Instance], query: str) -> list[EC2Instance]:
        query = (query or "").strip().lower()
        if not query:
            return list(instances)
        return [item for item in instances if any(query in value for value in (
            item.name.lower(), item.instance_id.lower(), item.platform_details.lower()))]

    @staticmethod
    def selected(instances: list[EC2Instance], selected_ids: set[str]) -> list[EC2Instance]:
        return [item for item in instances if item.instance_id in selected_ids]
