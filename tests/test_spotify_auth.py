from urllib.parse import parse_qs, urlparse

from dj_sync.spotify.auth import (
    build_authorization_url,
    generate_code_challenge,
    generate_code_verifier,
)


def test_code_verifier_uses_valid_length() -> None:
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128


def test_code_challenge_is_base64url_without_padding() -> None:
    challenge = generate_code_challenge("a" * 64)
    assert "=" not in challenge
    assert challenge


def test_authorization_url_contains_pkce_and_scopes() -> None:
    url = build_authorization_url(
        client_id="client-123",
        redirect_uri="http://127.0.0.1:8888/callback",
        code_challenge="challenge-123",
        state="state-123",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "accounts.spotify.com"
    assert query["client_id"] == ["client-123"]
    assert query["code_challenge_method"] == ["S256"]
    assert "playlist-read-private" in query["scope"][0]
    assert query["state"] == ["state-123"]


def test_refresh_access_token_preserves_existing_refresh_token(monkeypatch) -> None:
    from dj_sync.spotify.auth import refresh_access_token

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "access_token": "new-access",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

    def fake_post(url, *, data, timeout):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "refresh-123"
        assert data["client_id"] == "client-123"
        return Response()

    monkeypatch.setattr("dj_sync.spotify.auth.requests.post", fake_post)

    token = refresh_access_token(
        client_id="client-123",
        refresh_token="refresh-123",
    )

    assert token.access_token == "new-access"
    assert token.refresh_token == "refresh-123"
