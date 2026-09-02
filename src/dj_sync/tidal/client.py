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
    ) -> None:
        self.access_token = access_token
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_rate_limit_retries = max_rate_limit_retries
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self.sleep_fn = sleep_fn

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

    def _get_with_rate_limit_retry(
        self, url: str, *, params: dict[str, Any] | None = None
    ) -> requests.Response:
        for attempt in range(self.max_rate_limit_retries + 1):
            response = self.session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            if response.status_code != 429:
                response.raise_for_status()
                return response

            if attempt >= self.max_rate_limit_retries:
                response.raise_for_status()

            self.sleep_fn(self._rate_limit_delay(response, attempt))

        raise RuntimeError("Unreachable TIDAL retry state")

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

        response = self.session.post(
            f"{self.base_url}/playlists",
            headers=self._headers(mutation=True),
            json={
                "data": {
                    "type": "playlists",
                    "attributes": attributes,
                }
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return TidalPlaylist.from_resource(response.json()["data"])

    def get_playlist(self, playlist_id: str) -> TidalPlaylist:
        response = self.session.get(
            f"{self.base_url}/playlists/{playlist_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return TidalPlaylist.from_resource(response.json()["data"])

    def delete_playlist(self, playlist_id: str) -> None:
        response = self.session.delete(
            f"{self.base_url}/playlists/{playlist_id}",
            headers=self._headers(mutation=True),
            timeout=self.timeout,
        )
        response.raise_for_status()

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
