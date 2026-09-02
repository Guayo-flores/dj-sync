from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

import requests

SPOTIFY_API_BASE = "https://api.spotify.com/v1"


@dataclass(frozen=True, slots=True)
class SpotifyPlaylist:
    id: str
    name: str
    public: bool | None
    collaborative: bool
    owner_display_name: str | None
    item_count: int | None
    can_read_items: bool


class SpotifyClient:
    def __init__(
        self,
        access_token: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.access_token = access_token
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            f"{SPOTIFY_API_BASE}{path}",
            headers={"Authorization": f"Bearer {self.access_token}"},
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def iter_playlists(self) -> Iterator[SpotifyPlaylist]:
        offset = 0
        limit = 50
        while True:
            payload = self._get("/me/playlists", params={"limit": limit, "offset": offset})
            items = payload.get("items", [])
            for item in items:
                if not item:
                    continue
                owner = item.get("owner") or {}
                has_items_summary = "items" in item and item.get("items") is not None
                item_summary = item.get("items") or {}
                yield SpotifyPlaylist(
                    id=item["id"],
                    name=item["name"],
                    public=item.get("public"),
                    collaborative=bool(item.get("collaborative", False)),
                    owner_display_name=owner.get("display_name"),
                    item_count=item_summary.get("total"),
                    can_read_items=has_items_summary,
                )

            if not payload.get("next") or not items:
                break
            offset += len(items)

    def iter_playlist_items(self, playlist_id: str) -> Iterator[dict[str, Any]]:
        offset = 0
        limit = 100
        while True:
            payload = self._get(
                f"/playlists/{playlist_id}/items",
                params={"limit": limit, "offset": offset},
            )
            items = payload.get("items", [])
            yield from (item for item in items if item)
            if not payload.get("next") or not items:
                break
            offset += len(items)
