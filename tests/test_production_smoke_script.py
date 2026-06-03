from scripts.production_smoke import _json_or_text, _readiness_message


def test_json_or_text_parses_json_and_keeps_plain_text() -> None:
    assert _json_or_text('{"status":"ok"}') == {"status": "ok"}
    assert _json_or_text("plain") == "plain"


def test_readiness_message_prefers_failures() -> None:
    assert _readiness_message({"status": "not_ready", "failures": ["worker failed"]}) == "worker failed"
    assert _readiness_message({"status": "ready"}) == "ready"
