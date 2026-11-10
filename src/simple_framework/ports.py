from contextlib import AbstractContextManager
from typing import Protocol

from simple_framework.domain import Change, Record, Update


class Repository(Protocol):
    """Store core records and changes within serialised transactions."""

    def transaction(self) -> AbstractContextManager[None]:
        """Serialise checks and writes against concurrent transactions; roll back all on failure."""
        ...

    def get[T: Record](
        self,
        record_type: type[T],
        record_id: str,
        workspace_id: str | None = None,
    ) -> T | None:
        """Return a detached record by type, ID and optional workspace."""
        ...

    def records[T: Record](self, record_type: type[T], workspace_id: str | None = None) -> list[T]:
        """Return records in stable ID order within the requested scope."""
        ...

    def save(self, record: Record) -> None:
        """Copy the record into the active transaction; reject writes outside a transaction."""
        ...

    def append_change(self, update: Update, actor: str, client: str) -> None:
        """Append an immutable revision to the workspace change history."""
        ...

    def changes(self, workspace_id: str, after: int = 0) -> list[Change]:
        """Return detached changes after the exclusive position, in sequence order."""
        ...
