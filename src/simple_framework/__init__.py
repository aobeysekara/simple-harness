"""Public entry points for the provider-independent collaboration library.

Result and input record types live in ``simple_framework.domain``. Storage and
service adapters keep their explicit module imports; importing this package does
not load optional HTTP/MCP dependencies.
"""

from simple_framework.admin import Administration
from simple_framework.auth import authenticate
from simple_framework.domain import AccessDenied, Conflict, HarnessError, InvalidRequest
from simple_framework.harness import Harness
from simple_framework.ports import Repository

__all__ = [
    "AccessDenied",
    "Administration",
    "Conflict",
    "Harness",
    "HarnessError",
    "InvalidRequest",
    "Repository",
    "authenticate",
]
