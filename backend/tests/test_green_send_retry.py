from app.services.green_api_send import _is_transient_green_error


def test_transient_timeout_errors():
    assert _is_transient_green_error("URLError: <urlopen error timed out>")
    assert _is_transient_green_error("TimeoutError: The read operation timed out")
    assert _is_transient_green_error("GREEN API HTTP 503: busy")
    assert _is_transient_green_error("GREEN API HTTP 429: rate")


def test_non_transient_errors():
    assert not _is_transient_green_error("GREEN API HTTP 400: bad chatId")
    assert not _is_transient_green_error("GREEN API: нет idMessage в ответе")
    assert not _is_transient_green_error(None)
