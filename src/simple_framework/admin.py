from collections.abc import Callable
from dataclasses import replace
from secrets import token_urlsafe

from simple_framework.auth import token_digest
from simple_framework.domain import (
    Audit,
    Client,
    Conflict,
    Credential,
    InvalidRequest,
    Membership,
    Policy,
    Source,
    Task,
    User,
    Workspace,
)
from simple_framework.ports import Repository
from simple_framework.runtime import new_id, utc_now
from simple_framework.validation import require_text


class Administration:
    """Provision the local pilot as the trusted database operator."""

    def __init__(self, repository: Repository, clock: Callable[[], str] = utc_now) -> None:
        self.repository = repository
        self.clock = clock

    def _changed_policy(self, action: str, target_id: str, workspace_id: str = "") -> None:
        policy = self.repository.get(Policy, "access") or Policy(id="access", revision=0)
        self.repository.save(replace(policy, revision=policy.revision + 1))
        self.repository.save(
            Audit(
                id=new_id(),
                workspace_id=workspace_id,
                user="local-operator",
                client="local-admin",
                action=action,
                at=self.clock(),
                policy_revision=policy.revision + 1,
                target_id=target_id,
            )
        )

    def _workspace(self, workspace_id: str) -> None:
        if self.repository.get(Workspace, workspace_id) is None:
            raise InvalidRequest("Unknown workspace")

    def create_workspace(self, workspace_id: str, name: str) -> Workspace:
        """Create an empty workspace."""
        require_text(workspace_id, "Workspace ID")
        require_text(name, "Workspace name", 500)
        with self.repository.transaction():
            if self.repository.get(Workspace, workspace_id):
                raise Conflict("Workspace already exists")
            workspace = Workspace(id=workspace_id, name=name)
            self.repository.save(workspace)
            self._changed_policy("workspace.create", workspace_id, workspace_id)
            return workspace

    def create_task(self, workspace_id: str, task_id: str, title: str) -> Task:
        """Create a source-free task within a workspace."""
        require_text(task_id, "Task ID")
        require_text(title, "Task title", 500)
        with self.repository.transaction():
            self._workspace(workspace_id)
            if self.repository.get(Task, task_id, workspace_id):
                raise Conflict("Task already exists")
            task = Task(id=task_id, workspace_id=workspace_id, title=title)
            self.repository.save(task)
            self._changed_policy("task.create", task_id, workspace_id)
            return task

    def register_client(self, user: str, client_id: str, destination: str, role: str) -> str:
        """Register a client and issue its bearer token once."""
        for name, value in [("User", user), ("Client", client_id), ("Destination", destination)]:
            require_text(value, name)
        if role not in {"contributor", "reviewer", "validator"}:
            raise InvalidRequest("Role must be contributor, reviewer or validator")
        with self.repository.transaction():
            if self.repository.get(Client, client_id):
                raise Conflict("Client already exists")
            if self.repository.get(User, user) is None:
                self.repository.save(User(id=user))
            self.repository.save(
                Client(id=client_id, user=user, destination=destination, role=role)
            )
            token = token_urlsafe(32)
            self.repository.save(Credential(id=token_digest(token), user=user, client=client_id))
            self._changed_policy("client.register", client_id)
            return token

    def set_membership(self, workspace_id: str, user: str, active: bool) -> None:
        """Grant or revoke a user's workspace membership."""
        with self.repository.transaction():
            self._workspace(workspace_id)
            if self.repository.get(User, user) is None:
                raise InvalidRequest("Unknown user")
            self.repository.save(Membership(id=user, workspace_id=workspace_id, active=active))
            self._changed_policy(
                "membership.grant" if active else "membership.revoke", user, workspace_id
            )

    def set_client_active(self, client_id: str, active: bool) -> None:
        """Enable or revoke a registered client."""
        with self.repository.transaction():
            client = self.repository.get(Client, client_id)
            if client is None:
                raise InvalidRequest("Unknown client")
            self.repository.save(replace(client, active=active))
            self._changed_policy("client.enable" if active else "client.revoke", client_id)

    def set_user_active(self, user_id: str, active: bool) -> None:
        """Enable or revoke a user and all their client access."""
        with self.repository.transaction():
            user = self.repository.get(User, user_id)
            if user is None:
                raise InvalidRequest("Unknown user")
            self.repository.save(replace(user, active=active))
            self._changed_policy("user.enable" if active else "user.revoke", user_id)

    def revoke_credential(self, token: str) -> None:
        """Revoke a bearer token without storing its plaintext."""
        with self.repository.transaction():
            credential = self.repository.get(Credential, token_digest(token))
            if credential is None:
                raise InvalidRequest("Unknown credential")
            self.repository.save(replace(credential, active=False))
            self._changed_policy("credential.revoke", credential.id)

    def register_source(
        self,
        workspace_id: str,
        source_id: str,
        reference: str,
        users: list[str],
        clients: list[str],
    ) -> Source:
        """Register an immutable source reference with explicit grants."""
        require_text(source_id, "Source ID")
        require_text(reference, "Source reference", 2000)
        with self.repository.transaction():
            self._workspace(workspace_id)
            if self.repository.get(Source, source_id, workspace_id):
                raise Conflict("Source already exists")
            self._grants(users, clients)
            source = Source(
                id=source_id,
                workspace_id=workspace_id,
                reference=reference,
                users=sorted(set(users)),
                clients=sorted(set(clients)),
            )
            self.repository.save(source)
            self._changed_policy("source.register", source_id, workspace_id)
            return source

    def set_source_access(
        self,
        workspace_id: str,
        source_id: str,
        users: list[str],
        clients: list[str],
    ) -> None:
        """Replace the permitted users and clients for a source."""
        with self.repository.transaction():
            source = self.repository.get(Source, source_id, workspace_id)
            if source is None:
                raise InvalidRequest("Unknown source")
            self._grants(users, clients)
            self.repository.save(
                replace(source, users=sorted(set(users)), clients=sorted(set(clients)))
            )
            self._changed_policy("source.access", source_id, workspace_id)

    def _grants(self, users: list[str], clients: list[str]) -> None:
        if any(self.repository.get(User, user) is None for user in users):
            raise InvalidRequest("Unknown user in source grants")
        if any(self.repository.get(Client, client) is None for client in clients):
            raise InvalidRequest("Unknown client in source grants")
