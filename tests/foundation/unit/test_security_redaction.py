import json
import logging
import sys

from northstar_quant.foundation.observability.logging.logger import (
    ConsoleFormatter,
    JsonLinesFormatter,
    get_logger,
)
from northstar_quant.foundation.security import REDACTED, redact, redact_text


def test_redact_removes_recursive_secret_fields_and_url_passwords():
    safe = redact(
        {
            "token": "abc",
            "nested": {"password": "pw"},
            "url": "postgresql://user:pw@host/db?access_token=query-secret",  # secret-scan: allow; reason: disposable test fixture
        }
    )
    assert safe["token"] == REDACTED
    assert safe["nested"]["password"] == REDACTED
    assert "pw" not in safe["url"]
    assert "query-secret" not in safe["url"]


def test_redact_text_hides_auth_headers_and_json_like_values():
    source = 'Authorization: Bearer raw-token; {"client_secret": "raw-secret"}'  # secret-scan: allow; reason: disposable test fixture

    safe = redact_text(source)

    assert "raw-token" not in safe
    assert "raw-secret" not in safe
    assert safe.count(REDACTED) >= 2


def test_logging_formatters_redact_message_exception_and_direct_extra_fields():
    raw_secret = "not-for-output"  # secret-scan: allow; reason: disposable test fixture
    record = logging.LogRecord(
        "northstar.security-test",
        logging.ERROR,
        __file__,
        1,
        "broker callback failed: token=%s",
        (raw_secret,),
        None,
    )
    record.token = raw_secret
    record.connection = f"postgresql://northstar:{raw_secret}@db.example.test/northstar"

    console = ConsoleFormatter("%(message)s").format(record)
    payload = json.loads(JsonLinesFormatter().format(record))

    assert raw_secret not in console
    assert raw_secret not in json.dumps(payload)
    assert payload["token"] == REDACTED
    assert REDACTED in payload["msg"]


def test_logging_formatters_redact_exception_text():
    raw_secret = "not-for-exception-output"  # secret-scan: allow; reason: disposable test fixture
    try:
        raise RuntimeError(f"Authorization: Bearer {raw_secret}")  # secret-scan: allow; reason: disposable test fixture
    except RuntimeError:
        record = logging.LogRecord(
            "northstar.security-test",
            logging.ERROR,
            __file__,
            1,
            "callback failed",
            (),
            sys.exc_info(),
        )

    payload = json.loads(JsonLinesFormatter().format(record))

    assert raw_secret not in payload["exception"]
    assert REDACTED in payload["exception"]


def test_context_logger_redacts_direct_extra_before_creating_a_log_record():
    message, kwargs = get_logger("northstar.security-test").process(
        "api_key=not-for-context-output",  # secret-scan: allow; reason: disposable test fixture
        {"extra": {"token": "not-for-context-output"}},
    )

    assert "not-for-context-output" not in message
    assert kwargs["extra"]["token"] == REDACTED
