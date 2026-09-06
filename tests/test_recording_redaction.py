from smartops.recordings.redaction import redact_selector


def test_long_nexacro_selector_is_preserved_as_executable_metadata() -> None:
    selector = '[id="' + ".".join(f"container{index}" for index in range(40)) + '"]'

    saved = redact_selector(selector)

    assert len(selector) > 160
    assert saved == selector
    assert saved.endswith('"]')


def test_selector_with_secret_query_parameter_is_rejected_whole() -> None:
    assert redact_selector('a[href="/download?token=private"]') == "[redacted]"
