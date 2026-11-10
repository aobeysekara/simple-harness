from datetime import UTC, datetime
from secrets import token_urlsafe


def utc_now() -> str:
    """Return the current UTC time as an ISO timestamp."""
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    """Generate an opaque identifier."""
    return "r_" + token_urlsafe(24)
