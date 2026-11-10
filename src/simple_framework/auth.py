from hashlib import sha256

from simple_framework.domain import AccessDenied, Caller, Client, Credential, User
from simple_framework.ports import Repository


def token_digest(token: str) -> str:
    """Hash a high-entropy bearer token for storage."""
    return sha256(token.encode()).hexdigest()


def authenticate(repository: Repository, token: str) -> Caller:
    """Validate a pilot credential and return its bound identity."""
    with repository.transaction():
        credential = repository.get(Credential, token_digest(token))
        if credential is None or not credential.active:
            raise AccessDenied()
        caller = Caller(credential.user, credential.client, credential.id)
        check_identity(repository, caller)
        return caller


def check_identity(repository: Repository, caller: Caller) -> Client:
    """Recheck identity and revocation within the operation's transaction."""
    credential = repository.get(Credential, caller.credential)
    user = repository.get(User, caller.user)
    client = repository.get(Client, caller.client)
    if (
        credential is None
        or not credential.active
        or (credential.user, credential.client) != (caller.user, caller.client)
        or user is None
        or not user.active
        or client is None
        or not client.active
        or client.user != caller.user
    ):
        raise AccessDenied()
    return client
