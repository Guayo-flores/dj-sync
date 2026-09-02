from __future__ import annotations

import base64
import hashlib
import json
import secrets
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

DEFAULT_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
)


def generate_code_verifier(length: int = 64) -> str:
    if not 43 <= length <= 128:
        raise ValueError("PKCE code verifier length must be between 43 and 128")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
            "state": state,
        }
    )
    return f"{SPOTIFY_AUTHORIZE_URL}?{query}"


@dataclass(slots=True)
class SpotifyToken:
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600
    refresh_token: str | None = None
    scope: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpotifyToken":
        return cls(
            access_token=payload["access_token"],
            token_type=payload.get("token_type", "Bearer"),
            expires_in=int(payload.get("expires_in", 3600)),
            refresh_token=payload.get("refresh_token"),
            scope=payload.get("scope"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
        }


class SpotifyTokenStore:
    def __init__(self, path: str | Path = "data/spotify.token.json") -> None:
        self.path = Path(path)

    def save(self, token: SpotifyToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def load(self) -> SpotifyToken | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return SpotifyToken.from_payload(payload)


class _CallbackHandler(BaseHTTPRequestHandler):
    authorization_code: str | None = None
    returned_state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        type(self).authorization_code = params.get("code", [None])[0]
        type(self).returned_state = params.get("state", [None])[0]
        type(self).error = params.get("error", [None])[0]

        body = (
            "DJ Sync received Spotify authorization. "
            "You can close this browser tab and return to the terminal."
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        return


def exchange_authorization_code(
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    timeout: float = 20.0,
) -> SpotifyToken:
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return SpotifyToken.from_payload(response.json())


def refresh_access_token(
    *,
    client_id: str,
    refresh_token: str,
    timeout: float = 20.0,
) -> SpotifyToken:
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    payload.setdefault("refresh_token", refresh_token)
    return SpotifyToken.from_payload(payload)


def login_with_pkce(
    *,
    client_id: str,
    redirect_uri: str,
    token_store: SpotifyTokenStore,
) -> SpotifyToken:
    parsed_redirect = urlparse(redirect_uri)
    if parsed_redirect.scheme != "http" or parsed_redirect.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("Local V1 expects a loopback HTTP redirect URI")

    port = parsed_redirect.port
    if port is None:
        raise ValueError("Spotify redirect URI must include a port")

    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    state = secrets.token_urlsafe(24)
    authorization_url = build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        state=state,
    )

    _CallbackHandler.authorization_code = None
    _CallbackHandler.returned_state = None
    _CallbackHandler.error = None

    server = HTTPServer((parsed_redirect.hostname, port), _CallbackHandler)
    print("Opening Spotify authorization in your browser...")
    print(authorization_url)
    webbrowser.open(authorization_url)
    server.handle_request()
    server.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"Spotify authorization failed: {_CallbackHandler.error}")
    if _CallbackHandler.returned_state != state:
        raise RuntimeError("Spotify authorization state mismatch")
    if not _CallbackHandler.authorization_code:
        raise RuntimeError("Spotify authorization code was not returned")

    token = exchange_authorization_code(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code=_CallbackHandler.authorization_code,
        code_verifier=verifier,
    )
    token_store.save(token)
    return token
