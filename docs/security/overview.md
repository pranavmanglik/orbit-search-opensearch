# Orbit OpenSearch: security

The adapter enables TLS and certificate verification by default, requires explicit credentials when
the cluster requires authentication, signs opaque cursors, bounds page size and request time, and
normalizes provider failures. Application authorization, secret storage, network policy, and index
permissions remain deployment responsibilities.

## Transport and credentials

- Keep TLS and certificate verification enabled for remote clusters. Provide a trusted CA bundle
  with `ca_certs` for private PKI. Disabling TLS or verification must be an explicit local or
  isolated test decision.
- Load Basic Auth credentials from a secret manager. Avoid credentials in URLs, committed files,
  shell history, exception messages, and logs.
- Grant the service identity only the search and point-in-time operations required for its indices.
  This adapter does not administer users, roles, indices, or cluster configuration.
- Restrict cluster network access to application workloads that need it.

## Cursor handling

Cursors contain a PIT identifier and sort values. HMAC-SHA256 detects modification and the query
fingerprint binds a cursor to the original index and query, but the token is not encrypted. Treat it
as sensitive: avoid logging it or exposing it to untrusted parties. Use 32–64 unpredictable bytes
from a secret manager and share the key only among workers that continue the same cursor. Key
rotation invalidates outstanding cursors; PITs expire after one minute.

## Query and document data

Search text, filters, cursors, and source documents can contain confidential or personal data. Apply
authorization before the adapter call and derive tenant filters from trusted identity rather than
client-provided values. The capability and provider adapter do not enforce tenant isolation or
collection authorization. Configure mappings and index permissions to align with application
policy. Do not log document sources, full queries, raw provider responses, or cursor tokens.

Provider query values are structured by the adapter, but this does not replace application-level
validation or provider access control. Timeouts and bounded pages reduce per-request exposure; set
application concurrency limits for aggregate resource protection. No automatic retry is performed.

## Vulnerability disclosure

Use the repository [security policy](../../SECURITY.md) for private vulnerability reporting. Do not
publish unpatched security details in public issues.
