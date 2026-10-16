# Copyright 2026-present Orbit Contributors.
# Licensed under the Apache License, Version 2.0.
"""Bounded asynchronous OpenSearch implementation of ``orbit_search.SearchStore``."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from orbit_search import (
    SearchHit,
    SearchIndexNotFound,
    SearchOperationError,
    SearchPage,
    SearchQuery,
    validate_collection_name,
)

_PIT_TTL = "1m"
_MAX_CURSOR = 4_096
_MAX_RESPONSE_HITS = 101


class _AsyncClient(Protocol):
    async def create_pit(self, *, index: str, params: dict[str, str | float]) -> Any:
        """Open a point-in-time snapshot for the selected index."""

    async def delete_pit(self, *, body: dict[str, list[str]], params: dict[str, float]) -> Any:
        """Release a previously opened point-in-time snapshot."""

    async def search(self, **kwargs: Any) -> Any:
        """Execute a bounded search request against an index or snapshot."""

    async def close(self) -> Any:
        """Close the owned async OpenSearch transport."""


def _body(response: Any) -> Mapping[str, Any]:
    value = getattr(response, "body", response)
    if not isinstance(value, Mapping):
        raise SearchOperationError("OpenSearch returned an invalid response.")
    return value


def _stable_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _valid_sort_values(value: object) -> bool:
    """Accept only the finite score and non-negative document ordinal used by this adapter."""
    if not isinstance(value, list) or len(value) != 2:
        return False
    score, document_ordinal = value
    return (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(score)
        and isinstance(document_ordinal, int)
        and not isinstance(document_ordinal, bool)
        and document_ordinal >= 0
    )


class OpenSearchStore:
    """Search OpenSearch with async I/O, PIT consistency, and signed opaque cursors.

    The store owns the client passed to it and closes it from :meth:`aclose`. Cursors are bound
    to the configured secret so workers can share cursors, and expire with the one-minute PIT.
    The timeout bounds search and PIT requests. Cursor secrets must be stable across workers that
    exchange cursors and should be loaded from a secret manager.
    """

    def __init__(
        self,
        client: _AsyncClient,
        *,
        cursor_secret: bytes,
        request_timeout: float = 10.0,
    ) -> None:
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or not math.isfinite(request_timeout)
            or not 0.1 <= request_timeout <= 120
        ):
            raise ValueError("request_timeout must be between 0.1 and 120 seconds.")
        if not isinstance(cursor_secret, bytes) or not 32 <= len(cursor_secret) <= 64:
            raise ValueError("cursor_secret must contain 32 through 64 secret bytes.")
        self._client = client
        self._request_timeout = request_timeout
        self._signing_key = cursor_secret
        self._condition = asyncio.Condition()
        self._active = 0
        self._closing = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    @classmethod
    def connect(
        cls,
        hosts: str | Sequence[str],
        *,
        http_auth: tuple[str, str] | None = None,
        ca_certs: str | None = None,
        use_ssl: bool = True,
        verify_certs: bool = True,
        cursor_secret: bytes,
        request_timeout: float = 10.0,
    ) -> OpenSearchStore:
        """Create and own an official async client with explicit credentials and TLS settings.

        Certificate verification requires TLS. The defaults enable both TLS and certificate
        verification. ``http_auth`` and ``ca_certs`` are passed to the official client, and
        ``cursor_secret`` must contain 32–64 secret bytes shared by workers that exchange cursors.
        """
        if not isinstance(use_ssl, bool) or not isinstance(verify_certs, bool):
            raise TypeError("use_ssl and verify_certs must be bool.")
        if verify_certs and not use_ssl:
            raise ValueError("Certificate verification requires TLS.")
        try:
            opensearchpy = importlib.import_module("opensearchpy")
            async_client = opensearchpy.AsyncOpenSearch
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                "Install orbit-search-opensearch[opensearch] to use this adapter."
            ) from exc
        options: dict[str, Any] = {
            "hosts": hosts,
            "timeout": request_timeout,
            "use_ssl": use_ssl,
            "verify_certs": verify_certs,
        }
        if http_auth is not None:
            options["http_auth"] = http_auth
        if ca_certs is not None:
            options["ca_certs"] = ca_certs
        return cls(
            async_client(**options), cursor_secret=cursor_secret, request_timeout=request_timeout
        )

    async def search(self, collection: str, query: SearchQuery) -> SearchPage:
        """Search a collection and return at most the requested number of hits.

        A point-in-time snapshot and ``search_after`` provide forward pagination. The returned
        cursor is signed, opaque, query-bound, and valid only while its provider snapshot remains
        available. Provider timeouts, incomplete shards, malformed responses, and SDK failures are
        normalized to :class:`SearchOperationError` subclasses; task cancellation propagates.
        """
        index = validate_collection_name(collection)
        if not isinstance(query, SearchQuery):
            raise TypeError("query must be a SearchQuery.")
        await self._enter()
        pit_id: str | None = None
        keep_pit = False
        try:
            fingerprint = self._fingerprint(index, query)
            search_after: list[object] | None = None
            if query.cursor is None:
                try:
                    opened = _body(
                        await self._client.create_pit(
                            index=index,
                            params={
                                "keep_alive": _PIT_TTL,
                                "request_timeout": self._request_timeout,
                            },
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except SearchOperationError:
                    raise
                except Exception as exc:
                    self._raise_provider_error(exc)
                # OpenSearch calls this field ``pit_id`` (Elasticsearch uses ``id``).
                # Accept ``id`` as a compatibility fallback for injected clients that
                # implement the Elasticsearch-shaped response.
                pit_id = opened.get("pit_id", opened.get("id"))
                if not isinstance(pit_id, str) or not pit_id:
                    raise SearchOperationError("OpenSearch did not create a search snapshot.")
            else:
                pit_id, search_after = self._decode_cursor(query.cursor, fingerprint)

            request: dict[str, Any] = {
                "pit": {"id": pit_id, "keep_alive": _PIT_TTL},
                "query": self._query_body(query),
                "size": min(query.page_size + 1, _MAX_RESPONSE_HITS),
                # OpenSearch supports _doc as a PIT-local stable tiebreaker; _shard_doc
                # is Elasticsearch-specific and fails as an unmapped sort field here.
                "sort": [{"_score": "desc"}, {"_doc": "asc"}],
                "_source": True,
            }
            if search_after is not None:
                request["search_after"] = search_after
            try:
                result = _body(
                    await self._client.search(
                        body={
                            key: value for key, value in request.items() if key != "request_timeout"
                        },
                        params={
                            "timeout": f"{self._request_timeout}s",
                            # opensearch-py also interprets the request timeout from this
                            # transport parameter; without it its async HTTP client sees the
                            # server-side timeout string as its numeric socket timeout.
                            "request_timeout": self._request_timeout,
                            "allow_partial_search_results": "false",
                        },
                    )
                )
            except Exception as exc:
                self._raise_provider_error(exc)
            if "pit_id" in result:
                latest_pit = result.get("pit_id")
                if not isinstance(latest_pit, str) or not latest_pit:
                    raise SearchOperationError("OpenSearch returned invalid snapshot data.")
                # Providers may rotate the PIT ID on every search. Retain the newest one for
                # either the next signed cursor or final cleanup if response validation fails.
                pit_id = latest_pit
            if result.get("timed_out") is True:
                raise SearchOperationError("OpenSearch search timed out.", retryable=True)
            shard_info = result.get("_shards")
            if isinstance(shard_info, Mapping) and shard_info.get("failed", 0) != 0:
                raise SearchOperationError("OpenSearch search was incomplete.", retryable=True)
            hits_container = result.get("hits")
            raw_hits = hits_container.get("hits") if isinstance(hits_container, Mapping) else None
            if not isinstance(raw_hits, list) or len(raw_hits) > _MAX_RESPONSE_HITS:
                raise SearchOperationError("OpenSearch returned an invalid result page.")
            has_more = len(raw_hits) > query.page_size
            page_hits = raw_hits[: query.page_size]
            hits = tuple(self._hit(item) for item in page_hits)
            next_cursor = None
            if has_more:
                last_sort = page_hits[-1].get("sort")
                if not _valid_sort_values(last_sort):
                    raise SearchOperationError("OpenSearch returned invalid pagination data.")
                if pit_id is None:
                    raise SearchOperationError("OpenSearch returned invalid snapshot data.")
                next_cursor = self._encode_cursor(pit_id, last_sort, fingerprint)
            took_ms = result.get("took", 0)
            if isinstance(took_ms, bool) or not isinstance(took_ms, (int, float)) or took_ms < 0:
                raise SearchOperationError("OpenSearch returned invalid timing metadata.")
            keep_pit = has_more
            return SearchPage(hits=hits, next_cursor=next_cursor, took_ms=int(took_ms))
        finally:
            try:
                if pit_id is not None and not keep_pit:
                    await self._close_pit(pit_id)
            finally:
                await self._leave()

    async def aclose(self) -> None:
        """Stop accepting work, wait for active searches, and close the owned client.

        Calling this method again after successful shutdown is safe. If client shutdown fails, a
        normalized retryable ``SearchOperationError`` is raised and a later call may retry cleanup.
        """
        async with self._close_lock:
            async with self._condition:
                if self._closed:
                    return
                self._closing = True
                await self._condition.wait_for(lambda: self._active == 0)
            try:
                await self._client.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise SearchOperationError(
                    "OpenSearch client shutdown failed.", retryable=True
                ) from None
            else:
                async with self._condition:
                    self._closed = True

    async def _enter(self) -> None:
        async with self._condition:
            if self._closing or self._closed:
                raise SearchOperationError("OpenSearch search store is closing.")
            self._active += 1

    async def _leave(self) -> None:
        async with self._condition:
            self._active -= 1
            if self._active == 0:
                self._condition.notify_all()

    async def _close_pit(self, pit_id: str) -> None:
        try:
            await self._client.delete_pit(
                body={"pit_id": [pit_id]}, params={"request_timeout": self._request_timeout}
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return  # PIT expires server-side after one minute; cleanup must not mask search result.

    @staticmethod
    def _query_body(query: SearchQuery) -> dict[str, object]:
        filters: list[dict[str, object]] = []
        for field, value in query.filters.items():
            if value is None:
                filters.append({"bool": {"must_not": [{"exists": {"field": field}}]}})
            else:
                filters.append({"term": {field: value}})
        return {
            "bool": {
                "must": [{"multi_match": {"query": query.text, "fields": list(query.fields)}}],
                "filter": filters,
            }
        }

    @staticmethod
    def _hit(raw: object) -> SearchHit:
        if not isinstance(raw, Mapping):
            raise SearchOperationError("OpenSearch returned an invalid document.")
        identifier, score, source = raw.get("_id"), raw.get("_score"), raw.get("_source")
        if (
            not isinstance(identifier, str)
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
        ):
            raise SearchOperationError("OpenSearch returned invalid document metadata.")
        if not isinstance(source, Mapping):
            raise SearchOperationError("OpenSearch returned an invalid document source.")
        try:
            return SearchHit(id=identifier, score=float(score), source=source)
        except (TypeError, ValueError):
            raise SearchOperationError("OpenSearch returned an invalid document source.") from None

    @staticmethod
    def _raise_provider_error(exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        status = getattr(getattr(exc, "meta", None), "status", status)
        if status == 404:
            body = getattr(exc, "body", None)
            error = body.get("error") if isinstance(body, Mapping) else None
            if isinstance(error, Mapping) and error.get("type") == "index_not_found_exception":
                raise SearchIndexNotFound("Search collection was not found.") from None
            raise SearchOperationError("Search collection or snapshot was not found.") from None
        retryable = status in {408, 429, 500, 502, 503, 504}
        raise SearchOperationError(
            "OpenSearch search request failed.", retryable=retryable
        ) from None

    def _fingerprint(self, index: str, query: SearchQuery) -> str:
        payload = {
            "index": index,
            "text": query.text,
            "fields": query.fields,
            "filters": dict(query.filters),
            "page_size": query.page_size,
        }
        return hashlib.sha256(_stable_json(payload)).hexdigest()

    def _encode_cursor(self, pit_id: str, sort: list[object], fingerprint: str) -> str:
        if not _valid_sort_values(sort):
            raise SearchOperationError("OpenSearch returned invalid pagination data.")
        try:
            payload = _stable_json({"pit": pit_id, "sort": sort, "query": fingerprint})
        except (TypeError, ValueError):
            raise SearchOperationError("OpenSearch returned invalid pagination data.") from None
        signature = hmac.digest(self._signing_key, payload, "sha256")
        token = base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")
        if len(token) > _MAX_CURSOR:
            raise SearchOperationError("OpenSearch pagination state exceeds the cursor limit.")
        return token

    def _decode_cursor(self, token: str, fingerprint: str) -> tuple[str, list[object]]:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            payload, signature = raw[:-32], raw[-32:]
            if len(signature) != 32 or not hmac.compare_digest(
                signature, hmac.digest(self._signing_key, payload, "sha256")
            ):
                raise ValueError
            decoded = json.loads(payload)
            if (
                not isinstance(decoded, dict)
                or decoded.get("query") != fingerprint
                or not isinstance(decoded.get("pit"), str)
                or not isinstance(decoded.get("sort"), list)
                or not _valid_sort_values(decoded["sort"])
            ):
                raise ValueError
            return decoded["pit"], decoded["sort"]
        except (ValueError, TypeError, json.JSONDecodeError):
            raise SearchOperationError("Search cursor is invalid or expired.") from None
