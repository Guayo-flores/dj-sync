from dj_sync.matching.metadata import (
    artist_similarity,
    is_safe_automatic_match,
    normalize_text,
    score_metadata_match,
)


def test_normalize_text_handles_case_accents_and_punctuation() -> None:
    assert normalize_text("BÉLICA!!!") == "belica"


def test_artist_similarity_handles_featured_artist_formatting() -> None:
    score = artist_similarity("Bad Bunny, Feid", ("Bad Bunny", "Feid"))
    assert score == 1.0


def test_safe_match_requires_close_duration_and_strong_metadata() -> None:
    score = score_metadata_match(
        spotify_title="NUEVAYoL",
        spotify_artist="Bad Bunny",
        spotify_duration_ms=183000,
        tidal_title="NUEVAYoL",
        tidal_artists=("Bad Bunny",),
        tidal_duration_ms=184000,
    )
    assert score.total > 0.94
    assert is_safe_automatic_match(score)


def test_remix_title_is_not_treated_as_safe_original_match() -> None:
    score = score_metadata_match(
        spotify_title="Pepas",
        spotify_artist="Farruko",
        spotify_duration_ms=287000,
        tidal_title="Pepas - Tiësto Remix",
        tidal_artists=("Farruko", "Tiësto"),
        tidal_duration_ms=203000,
    )
    assert not is_safe_automatic_match(score)
