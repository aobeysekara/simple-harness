# Handover fixtures

Small synthetic requests for dependency validation. No conversation records or external evidence files are included.

- `a-initial.json` names two contributions but declares neither. One contribution has a restricted source.
- `c.json` names two contributions but declares only one.

The tests create all identities, grants and records locally. Both requests must fail without partial writes, then succeed when the caller supplies the exact permitted dependencies.
