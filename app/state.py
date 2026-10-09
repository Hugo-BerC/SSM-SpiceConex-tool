from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class AppState:
    """UI-independent application state shared by controllers and views."""
    profile: str = ""
    region: str = ""
    instances: list = field(default_factory=list)
    checked_instance_ids: set[str] = field(default_factory=set)
    connectivity_rows: list = field(default_factory=list)
    connectivity_results: list = field(default_factory=list)
    connectivity_csv_path: str = ""

    def set_instances(self, instances):
        self.instances = list(instances)
        valid_ids = {item.instance_id if hasattr(item, "instance_id") else item.get("InstanceID") for item in self.instances}
        self.checked_instance_ids.intersection_update(valid_ids)

    def clear_selection(self):
        self.checked_instance_ids.clear()

    def toggle_instance(self, instance_id: str) -> bool:
        if instance_id in self.checked_instance_ids:
            self.checked_instance_ids.remove(instance_id)
            return False
        self.checked_instance_ids.add(instance_id)
        return True
