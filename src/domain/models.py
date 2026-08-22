"""Domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from src.domain.enums import ProfileRole, ShowStatus


@dataclass
class Show:
    """Show/Event domain model."""
    show_id: str
    name: str
    url: str
    target_total: int = 1
    max_per_order: int = 1
    ticket_index: Optional[int] = None
    raw_row: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AdsPowerProfile:
    """AdsPower profile representation."""
    user_id: str
    name: str
    serial_number: str
    group_id: str
    group_name: str
    proxy_host: str = ""
    proxy_port: str = ""
    proxy_user: str = ""
    proxy_password: str = ""
    proxy_type: str = "http"
    role: ProfileRole = ProfileRole.ACTIVE
    ws_endpoint: Optional[str] = None
    is_open: bool = False
    last_status: str = "idle"
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def proxy_key(self) -> str:
        """Normalized proxy address string."""
        if self.proxy_host and self.proxy_port:
            return f"{self.proxy_host}:{self.proxy_port}"
        return self.user_id


@dataclass
class CheckResult:
    """Result of checking an event."""
    show_id: str
    name: str
    url: str
    status: ShowStatus
    target: int
    reserved: int
    available_approx: str = ""
    details: str = ""
    notes: str = ""
    screenshot_path: Optional[str] = None

    def to_report_row(self) -> Dict[str, Any]:
        """Format row for report.csv (excluding internal url for clean report)."""
        return {
            "name": self.name,
            "status": self.status.value,
            "target": self.target,
            "reserved": self.reserved,
            "available_approx": self.available_approx,
            "details": self.details,
            "notes": self.notes,
        }
