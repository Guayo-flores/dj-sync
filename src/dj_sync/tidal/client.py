from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
from typing import Any, Callable
from uuid import uuid4

from dj_sync.matching.isrc import normalize_isrc

import requests

TIDAL_API_BASE_URL = "https://openapi.tidal.com/v2"
JSON_API_CONTENT_TYPE = "application/vnd.api+json"


@dataclass(frozen=True, slots=True)
class TidalPlaylist:
    id: str
    name: str
    description: str | None = None
    access_type: str | None = None

    @classmethod
    def from_resource(cls, resource: dict[str, Any]) -> "TidalPlaylist":
        attributes = resource.get("attributes") or {}
        return cls(
            id=str(resource["id"]),
            name=str(attributes.get("name") or ""),
            description=attributes.get("description"),
            access_type=attributes.get("accessType"),
        )


@dataclass(frozen=True, slots=True)
class TidalPlaylistItem:
    id: str
    type: str
    item_id: str | None = None

    @classmethod
    def from_resource(cls, resource: dict[str, Any]) -> "TidalPlaylistItem":
        meta = resource.get("meta") or {}
        return cls(
            id=str(resource["id"]),
            type=str(resource.get("type") or ""),
            item_id=str(meta["itemId"]) if meta.get("itemId") else None,
        )


@dataclass(frozen=True, slots=True)
class TidalPlaylistAddResult:
    added: int
    skipped_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TidalTrack:
    id: str
    title: str
    isrc: str | None
    duration: str | None = None
    artists: tuple[str, ...] = ()

    @classmethod
    def from_resource(cls, resource: dict[str, Any]) -> "TidalTrack":
        attributes = resource.get("attributes") or {}
        return cls(
            id=str(resource["id"]),
            title=str(attributes.get("title") or ""),
            isrc=attributes.get("isrc"),
            duration=attributes.get("duration"),
        )

    @property
    def duration_ms(self) -> int | None:
        if not self.duration:
            return None
        match = re.fullmatch(
            r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?",
            self.duration,
        )
        if match is None:
            return None
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes") or 0)
        seconds = float(match.group("seconds") or 0)
        return round(((hours * 60 + minutes) * 60 + seconds) * 1000)


class TidalClient:
    def __init__(
        self,
        access_token: str,
        *,
        session: requests.Session | None = None,
        base_url: str = TIDAL_API_BASE_URL,
        timeout: float = 20.0,
        max_rate_limit_retries: int = 6,
        rate_limit_backoff_seconds: float = 2.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        token_refresher: Callable[[], str] | None = None,
    ) -> None:
        self.access_token = access_token
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_rate_limit_retries = max_rate_limit_retries
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self.sleep_fn = sleep_fn
        self.token_refresher = token_refresher

    def _headers(self, *, mutation: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": JSON_API_CONTENT_TYPE,
            "Content-Type": JSON_API_CONTENT_TYPE,
        }
        if mutation:
            headers["Idempotency-Key"] = str(uuid4())
        return headers

    def _rate_limit_delay(self, response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass

        # TIDAL does not publish one fixed public request-per-second quota.
        # When Retry-After is absent, back off exponentially and cap each wait
        # so a large first-time library match can recover without hammering the API.
        return min(self.rate_limit_backoff_seconds * (2**attempt), 30.0)

    def _refresh_access_token_once(self) -> None:
        if self.token_refresher is None:
            raise RuntimeError(
                "TIDAL session is no longer authorized. Run: dj-sync tidal-login"
            )
        self.access_token = self.token_refresher()

    def _get_with_rate_limit_retry(
        self, url: str, *, params: dict[str, Any] | None = None
    ) -> requests.Response:
        rate_attempt = 0
        auth_refreshed = False
        while True:
            response = self.session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )

            # TIDAL user-resource requests can reject a stale user token. Refresh
            # once, persist the new token through the callback, and replay the
            # original request. A genuine permission error will still surface on
            # the retried request instead of looping forever.
            if response.status_code in {401, 403} and not auth_refreshed:
                self._refresh_access_token_once()
                auth_refreshed = True
                continue

            if response.status_code != 429:
                response.raise_for_status()
                return response

            if rate_attempt >= self.max_rate_limit_retries:
                response.raise_for_status()

            self.sleep_fn(self._rate_limit_delay(response, rate_attempt))
            rate_attempt += 1

    def _mutation_with_rate_limit_retry(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> requests.Response:
        # Generate the idempotency key once and reuse it across every retry,
        # including an auth refresh. If TIDAL received the first mutation before
        # the response failed, the same key prevents a duplicate write.
        idempotency_key = str(uuid4())
        request = getattr(self.session, method.lower())
        rate_attempt = 0
        auth_refreshed = False
        while True:
            headers = self._headers()
            headers["Idempotency-Key"] = idempotency_key
            kwargs: dict[str, Any] = {
                "headers": headers,
                "timeout": self.timeout,
            }
            if json is not None:
                kwargs["json"] = json
            response = request(url, **kwargs)

            if response.status_code in {401, 403} and not auth_refreshed:
                self._refresh_access_token_once()
                auth_refreshed = True
                continue

            if response.status_code != 429:
                response.raise_for_status()
                return response

            if rate_attempt >= self.max_rate_limit_retries:
                response.raise_for_status()

            self.sleep_fn(self._rate_limit_delay(response, rate_attempt))
            rate_attempt += 1

    def create_playlist(
        self,
        name: str,
        *,
        description: str | None = None,
        access_type: str = "UNLISTED",
    ) -> TidalPlaylist:
        attributes: dict[str, Any] = {
            "name": name,
            "accessType": access_type,
        }
        if description is not None:
            attributes["description"] = description

        response = self._mutation_with_rate_limit_retry(
            "post",
            f"{self.base_url}/playlists",
            json={
                "data": {
                    "type": "playlists",
                    "attributes": attributes,
                }
            },
        )
        return TidalPlaylist.from_resource(response.json()["data"])

    def get_playlist(self, playlist_id: str) -> TidalPlaylist:
        response = self._get_with_rate_limit_retry(
            f"{self.base_url}/playlists/{playlist_id}"
        )
        return TidalPlaylist.from_resource(response.json()["data"])

    def delete_playlist(self, playlist_id: str) -> None:
        self._mutation_with_rate_limit_retry(
            "delete", f"{self.base_url}/playlists/{playlist_id}"
        )

    @staticmethod
    def _next_link(payload: dict[str, Any]) -> str | None:
        next_link = (payload.get("links") or {}).get("next")
        if isinstance(next_link, str):
            return next_link
        if isinstance(next_link, dict) and next_link.get("href"):
            return str(next_link["href"])
        return None

    def _resolve_api_link(self, link: str | None) -> str | None:
        if link is None:
            return None
        if link.startswith(("http://", "https://")):
            return link
        # TIDAL pagination links may be API-relative (for example
        # /playlists?...). They are relative to the configured v2 API base,
        # not the host root, so preserve the /v2 prefix in self.base_url.
        return f"{self.base_url}/{link.lstrip('/')}"

    def iter_owned_playlists(self):
        url: str | None = f"{self.base_url}/playlists"
        params: dict[str, Any] | None = {"filter[owners.id]": ["me"]}
        while url:
            response = self._get_with_rate_limit_retry(url, params=params)
            payload = response.json()
            for resource in payload.get("data") or []:
                yield TidalPlaylist.from_resource(resource)
            url = self._resolve_api_link(self._next_link(payload))
            # The next link already contains TIDAL's opaque cursor and filters.
            params = None

    def iter_playlist_items(self, playlist_id: str):
        url: str | None = f"{self.base_url}/playlists/{playlist_id}/relationships/items"
        while url:
            response = self._get_with_rate_limit_retry(url)
            payload = response.json()
            for resource in payload.get("data") or []:
                yield TidalPlaylistItem.from_resource(resource)
            url = self._resolve_api_link(self._next_link(payload))

    def add_playlist_tracks(
        self,
        playlist_id: str,
        track_ids: list[str],
        *,
        position_before: str | None = None,
    ) -> TidalPlaylistAddResult:
        if not track_ids:
            return TidalPlaylistAddResult(added=0)
        if len(track_ids) > 50:
            raise ValueError("TIDAL accepts at most 50 playlist items per add request")

        payload: dict[str, Any] = {
            "data": [
                {"type": "tracks", "id": str(track_id)}
                for track_id in track_ids
            ]
        }
        if position_before is not None:
            payload["meta"] = {"positionBefore": position_before}

        response = self._mutation_with_rate_limit_retry(
            "post",
            f"{self.base_url}/playlists/{playlist_id}/relationships/items",
            json=payload,
        )
        payload = response.json()
        skipped = tuple(
            str(item["id"])
            for item in (payload.get("meta") or {}).get("skipped") or []
        )
        added = len(payload.get("data") or [])
        # Some compatible responses may omit relationship data but still report
        # skipped identifiers. Derive the successful count in that case.
        if added == 0 and track_ids and "data" not in payload:
            added = len(track_ids) - len(skipped)
        return TidalPlaylistAddResult(added=added, skipped_ids=skipped)


    def remove_playlist_items(
        self, playlist_id: str, items: list[TidalPlaylistItem]
    ) -> None:
        if not items:
            return
        if len(items) > 50:
            raise ValueError("TIDAL accepts at most 50 playlist items per remove request")
        if any(item.item_id is None for item in items):
            raise ValueError("TIDAL playlist itemId is required to remove an occurrence")

        self._mutation_with_rate_limit_retry(
            "delete",
            f"{self.base_url}/playlists/{playlist_id}/relationships/items",
            json={
                "data": [
                    {
                        "type": item.type,
                        "id": item.id,
                        "meta": {"itemId": item.item_id},
                    }
                    for item in items
                ]
            },
        )

    def update_playlist_name(self, playlist_id: str, name: str) -> TidalPlaylist:
        response = self._mutation_with_rate_limit_retry(
            "patch",
            f"{self.base_url}/playlists/{playlist_id}",
            json={
                "data": {
                    "type": "playlists",
                    "id": playlist_id,
                    "attributes": {"name": name},
                }
            },
        )
        payload = response.json()
        data = payload.get("data")
        if isinstance(data, dict) and data.get("id") is not None:
            return TidalPlaylist.from_resource(data)
        return self.get_playlist(playlist_id)

    def get_tracks_by_isrc(self, isrcs: list[str]) -> list[TidalTrack]:
        if not isrcs:
            return []
        if len(isrcs) > 20:
            raise ValueError("TIDAL accepts at most 20 ISRCs per tracks request")

        normalized_isrcs = []
        for isrc in isrcs:
            normalized = normalize_isrc(isrc)
            if normalized is None:
                raise ValueError(f"Invalid ISRC: {isrc!r}")
            if normalized not in normalized_isrcs:
                normalized_isrcs.append(normalized)

        response = self._get_with_rate_limit_retry(
            f"{self.base_url}/tracks",
            params={"filter[isrc]": normalized_isrcs},
        )
        data = response.json().get("data") or []
        return [TidalTrack.from_resource(resource) for resource in data]

    def search_tracks(self, query: str, *, limit: int = 10) -> list[TidalTrack]:
        """Search TIDAL and return ranked track candidates with artist metadata."""
        if not query.strip():
            return []
        if limit < 1:
            return []

        response = self._get_with_rate_limit_retry(
            f"{self.base_url}/searchResults",
            params={
                "filter[query]": query[:256],
                "include": ["tracks", "tracks.artists"],
            },
        )
        payload = response.json()
        search_resources = payload.get("data") or []
        if not search_resources:
            return []

        track_relationship = (
            (search_resources[0].get("relationships") or {}).get("tracks") or {}
        )
        relationship_data = track_relationship.get("data") or []
        ranked_ids = [
            str(identifier.get("id"))
            for identifier in relationship_data
            if identifier.get("type") == "tracks" and identifier.get("id") is not None
        ][:limit]

        included = payload.get("included") or []
        resources = {
            (str(resource.get("type")), str(resource.get("id"))): resource
            for resource in included
            if resource.get("type") is not None and resource.get("id") is not None
        }
        artist_names = {
            str(resource["id"]): str((resource.get("attributes") or {}).get("name") or "")
            for resource in included
            if resource.get("type") == "artists" and resource.get("id") is not None
        }

        tracks: list[TidalTrack] = []
        for track_id in ranked_ids:
            resource = resources.get(("tracks", track_id))
            if resource is None:
                continue
            track = TidalTrack.from_resource(resource)
            artist_relationship = (
                ((resource.get("relationships") or {}).get("artists") or {}).get("data")
                or []
            )
            artists = tuple(
                artist_names.get(str(identifier.get("id")), "")
                for identifier in artist_relationship
                if identifier.get("id") is not None
                and artist_names.get(str(identifier.get("id")), "")
            )
            tracks.append(
                TidalTrack(
                    id=track.id,
                    title=track.title,
                    isrc=track.isrc,
                    duration=track.duration,
                    artists=artists,
                )
            )
        return tracks
