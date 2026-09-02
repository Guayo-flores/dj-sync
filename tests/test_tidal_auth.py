from urllib.parse import parse_qs, urlparse

from dj_sync.tidal.auth import (
    DEFAULT_SCOPES,
    TidalToken,
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


def test_authorization_url_contains_pkce_and_required_scopes() -> None:
    url = build_authorization_url(
        client_id="tidal-client-123",
        redirect_uri="http://127.0.0.1:8889/callback",
        code_challenge="challenge-123",
        state="state-123",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.netloc == "login.tidal.com"
    assert query["client_id"] == ["tidal-client-123"]
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == set(DEFAULT_SCOPES)
    assert query["state"] == ["state-123"]


def test_tidal_token_preserves_refresh_token() -> None:
    token = TidalToken.from_payload(
        {
            "access_token": "access-123",
            "refresh_token": "refresh-123",
            "expires_in": 86400,
            "scope": "playlists.read playlists.write",
        }
    )

    assert token.access_token == "access-123"
    assert token.refresh_token == "refresh-123"
    assert token.expires_in == 86400
