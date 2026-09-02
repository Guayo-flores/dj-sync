from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

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


class TidalClient:
    def __init__(
        self,
        access_token: str,
        *,
        session: requests.Session | None = None,
        base_url: str = TIDAL_API_BASE_URL,
        timeout: float = 20.0,
    ) -> None:
        self.access_token = access_token
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self, *, mutation: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": JSON_API_CONTENT_TYPE,
            "Content-Type": JSON_API_CONTENT_TYPE,
        }
        if mutation:
            headers["Idempotency-Key"] = str(uuid4())
        return headers

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
