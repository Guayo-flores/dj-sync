from dj_sync.sync.diff import diff_track_ids


def test_diff_detects_additions_and_removals() -> None:
    result = diff_track_ids(
        current=["spotify-a", "spotify-c", "spotify-d"],
        previous=["spotify-a", "spotify-b", "spotify-c"],
    )

    assert result.added == {"spotify-d"}
    assert result.removed == {"spotify-b"}
    assert result.unchanged == {"spotify-a", "spotify-c"}
    assert result.has_changes is True


def test_diff_is_idempotent_when_state_is_unchanged() -> None:
    result = diff_track_ids(
        current=["spotify-a", "spotify-b"],
        previous=["spotify-a", "spotify-b"],
    )

    assert result.added == set()
    assert result.removed == set()
    assert result.has_changes is False
