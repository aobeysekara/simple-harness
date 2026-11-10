from simple_framework.domain import InvalidRequest


def require_text(value: str, name: str, maximum: int = 128) -> None:
    """Reject empty or oversized input text."""
    if not value.strip() or len(value) > maximum:
        raise InvalidRequest(f"{name} must contain between 1 and {maximum} characters")


def require_limit(limit: int) -> None:
    """Require a bounded response page."""
    if not 1 <= limit <= 100:
        raise InvalidRequest("Page size must be between 1 and 100")
