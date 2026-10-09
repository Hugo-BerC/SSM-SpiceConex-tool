from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class EC2Instance:
    instance_id: str
    name: str
    private_ip: str | None
    platform_details: str
    state: str

    @classmethod
    def from_boto(cls, instance: dict) -> "EC2Instance":
        tags = instance.get("Tags", [])
        name = next((tag.get("Value") for tag in tags if tag.get("Key") == "Name"), "No Name")
        return cls(
            instance_id=instance["InstanceId"],
            name=name,
            private_ip=instance.get("PrivateIpAddress"),
            platform_details=instance.get("PlatformDetails", "Unknown"),
            state=instance.get("State", {}).get("Name", "unknown"),
        )

    def as_legacy_dict(self) -> dict:
        return {
            "Name": self.name,
            "InstanceID": self.instance_id,
            "PrivateIpAddress": self.private_ip or "No IP",
            "PlatformDetails": self.platform_details,
            "InstanceState": self.state,
        }
