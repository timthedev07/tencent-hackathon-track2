from worker.tasks.normalise import _normalise_content


def test_title_is_used_for_title_only_source_objects() -> None:
    assert _normalise_content("", "An empty but named page") == "An empty but named page"


def test_content_remains_preferred_to_title() -> None:
    assert _normalise_content("Page body", "Page title") == "Page body"
