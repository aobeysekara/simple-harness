# Security

SIMple is an experimental collaboration harness for small, trusted groups. It is not a production identity service or a hostile-code sandbox.

## Reporting

Do not post vulnerability details or credentials in public issues. Use GitHub's **Security → Report a vulnerability** when available. Otherwise open an issue titled **Private security contact requested**, without details, and agree a private channel with the maintainer.

Only the current alpha is maintained. No response-time commitment or long-term support policy is established.

## Trust boundaries

- The local operator can read and change the database. Independent clients connect through the authenticated service and do not receive the database file.
- Keep tokens separate and reserve reviewer credentials for review. Tokens have no automatic expiry or refresh. Revoke lost credentials.
- The service binds to loopback by default. Use an encrypted tunnel or an HTTPS proxy for other machines.
- Permissions apply to current reads, writes, history and retries. They cannot recall information already released.
- Contributors declare sources. The harness propagates those restrictions but cannot detect undeclared sensitive content.
- Accepted text is reference material, not permission to execute commands, deploy or publish.
- The replay operator and checked-in test code remain trusted. Hashes identify bytes, not truth. Run untrusted code in a suitable external sandbox.

Operators manage backups, retention and deletion. Use synthetic data for first evaluations. Read the [service guide](docs/shared-service.md) before connecting clients.
