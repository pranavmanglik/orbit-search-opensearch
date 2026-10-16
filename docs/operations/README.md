# Orbit OpenSearch: operations

`orbit-search-opensearch` implements the `SearchStore` capability with the official asynchronous
`opensearch-py` client. Install the provider capability and adapter extra:

```bash
python -m pip install 'orbit-search' 'orbit-search-opensearch[opensearch]'
```

`OpenSearchStore.connect` accepts hosts, optional HTTP Basic Auth, CA bundle, explicit TLS settings,
cursor signing secret, and request timeout. TLS and certificate verification are enabled by default;
certificate verification cannot be enabled with TLS disabled. If you inject a client, configure its
TLS and authentication yourself. Search requests apply both the OpenSearch server-side `timeout` and
the transport `request_timeout`; PIT creation and deletion also receive the configured transport
timeout.

## Query and pagination

The adapter translates query text to `multi_match` over the requested fields. Exact filters map to
`term`; a `None` filter means the field must not exist. Index mappings must support those operations.
The adapter does not provision indices, manage mappings, ingest data, normalize relevance scores, or
retry automatically. Timed-out and partial-shard responses are returned as retryable errors instead
of incomplete pages.

Pagination uses point-in-time snapshots with `search_after`, sorted by score and OpenSearch's `_doc`
PIT-local tie breaker. PIT keep-alive is one minute. Continuation cursors are signed with HMAC-SHA256
and bound to the index and query parameters. They are opaque, contain provider snapshot state, and
expire when the PIT expires. All workers exchanging cursors must share the same 32–64 byte secret.
Changing the key invalidates existing cursors. OpenSearch can rotate the PIT identifier in a search
response; the adapter tracks the newest identifier for continuation and closes that latest snapshot
on the final page or an error. PIT deletion failures are ignored because the provider expires
snapshots; do not interpret that behavior as proof that cleanup succeeded. Before signing a
continuation cursor, it validates the provider sort tuple as a finite score and non-negative document
ordinal; malformed pagination metadata fails the current search.

`aclose()` stops accepting new searches, waits for active operations, and closes the owned async
client. Call it during application shutdown. Successful shutdown is idempotent; an SDK shutdown
failure raises a normalized retryable error and can be retried.

## Live integration check

The default tests use a fake client. The opt-in live test creates a uniquely named index, checks
filtered PIT pagination, and removes the index. Configure `ORBIT_OPENSEARCH_URL` for a disposable
service. Remote targets require `ORBIT_OPENSEARCH_ALLOW_REMOTE_TESTS=1`. Optional credentials and
trust roots are `ORBIT_OPENSEARCH_USERNAME`, `ORBIT_OPENSEARCH_PASSWORD`, and
`ORBIT_OPENSEARCH_CA_CERTS`. Do not use production credentials or an index with user data.

Before deployment, verify client/server compatibility, least-privilege search and PIT permissions,
TLS trust, field mappings, cursor-secret rotation, connection limits, and behavior under concurrent
queries. Local tests do not certify a production cluster or service topology.
