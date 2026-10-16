# Orbit OpenSearch: development

The manifest declares support for Python `>=3.11,<3.15`. Check workflow results before describing
which versions have actually been exercised in CI.

## Local checks

From this repository in the Orbit workspace:

```bash
python -m pip install -e ../orbit-search
python -m pip install -e '.[dev,opensearch]'
pytest
ruff check src tests
ruff format --check src tests
mypy
```

The default tests use a fake client and need no OpenSearch service. The live integration test skips
when `ORBIT_OPENSEARCH_URL` is absent. When enabled, it creates and deletes a randomly named index;
use a disposable cluster and narrowly scoped credentials. Remote targets require the explicit
`ORBIT_OPENSEARCH_ALLOW_REMOTE_TESTS=1` opt-in.

The current local quality matrix passes on CPython 3.11, 3.12, 3.13, and 3.14: 7 fake-client tests
pass on each interpreter, and the opt-in live OpenSearch test is skipped when no endpoint is
configured. Ruff lint and format checks, strict mypy, and wheel/sdist builds also pass for each
version. These are local checks, not hosted CI evidence.

## Contribution guidance

Add focused tests when changing query translation, request timeout propagation, PIT cleanup,
cursor authentication, response validation, error normalization, or client shutdown. Keep provider
tests deterministic with fakes and use the live test for SDK/server behavior. Update the README and
relevant architecture, operations, or security guide when observable behavior changes. Record the
exact OpenSearch and client versions for live checks; a local check is not evidence of hosted CI or
production validation.
