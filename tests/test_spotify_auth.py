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
