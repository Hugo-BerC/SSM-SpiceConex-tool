from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class EC2Instance:
    instance_id: str
    name: str
    private_ip: str | None
    platform_details: str
    state: str
    ssm_managed: bool | None = None
    ssm_ping_status: str = ""
    ssm_last_ping: str = ""
    ssm_agent_version: str = ""

    @property
    def session_ready(self) -> bool:
        """Whether AWS confirms this instance can accept an SSM session now."""
        return (
            self.state.casefold() == "running"
            and self.ssm_managed is True
            and self.ssm_ping_status.casefold() == "online"
        )

    @property
    def session_action_eligible(self) -> bool:
        """Whether the UI may offer an SSM action for this instance.

        ``None`` preserves access for roles that can start SSM sessions but
        cannot call ``ssm:DescribeInstanceInformation``. AWS remains the
        authority for the final start-session decision in that case.
        """
        return self.session_ready or self.ssm_managed is None

    @property
    def ssm_status_label(self) -> str:
        if self.ssm_managed is None:
            return "SSM UNKNOWN"
        if not self.ssm_managed:
            return "NOT MANAGED"
        return f"SSM {self.ssm_ping_status.upper() or 'UNKNOWN'}"

    @property
    def ssm_status_key(self) -> str:
        if self.ssm_managed is None:
            return "unknown"
        if not self.ssm_managed:
            return "unmanaged"
        return self.ssm_ping_status.casefold() or "unknown"

    @property
    def ssm_last_ping_label(self) -> str:
        """Compact, readable representation of the last SSM heartbeat."""
        if not self.ssm_last_ping:
            return "not reported"
        # boto3 normally provides an aware datetime.  Keeping only seconds
        # makes the fleet row useful without consuming the complete layout.
        value = self.ssm_last_ping.replace("T", " ").replace("+00:00", " UTC")
        return value[:23]

    @classmethod
    def from_boto(cls, instance: dict, ssm_information: dict | None = None,
                  ssm_inventory_available: bool = True) -> "EC2Instance":
        tags = instance.get("Tags", [])
        name = next((tag.get("Value") for tag in tags if tag.get("Key") == "Name"), "No Name")
        ssm_information = ssm_information or {}
        return cls(
            instance_id=instance["InstanceId"],
            name=name,
            private_ip=instance.get("PrivateIpAddress"),
            platform_details=instance.get("PlatformDetails", "Unknown"),
            state=instance.get("State", {}).get("Name", "unknown"),
            ssm_managed=bool(ssm_information) if ssm_inventory_available else None,
            ssm_ping_status=ssm_information.get("PingStatus", ""),
            ssm_last_ping=str(ssm_information.get("LastPingDateTime", "") or ""),
            ssm_agent_version=ssm_information.get("AgentVersion", ""),
        )

    def as_legacy_dict(self) -> dict:
        return {
            "Name": self.name,
            "InstanceID": self.instance_id,
            "PrivateIpAddress": self.private_ip or "No IP",
            "PlatformDetails": self.platform_details,
            "InstanceState": self.state,
            "SSMManaged": self.ssm_managed,
            "SSMPingStatus": self.ssm_ping_status or "Unknown",
            "SSMLastPing": self.ssm_last_ping or "",
        }
