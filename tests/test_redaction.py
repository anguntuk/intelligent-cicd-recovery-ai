from cicd_recovery.redaction import redact_text


def test_redacts_common_tokens_and_credentials() -> None:
    source = (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456 "
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890 "
        "password=hunter2 aws_secret_access_key=verysecret"
    )
    result = redact_text(source)
    assert "abcdefghijklmnopqrstuvwxyz123456" not in result
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in result
    assert "hunter2" not in result
    assert "verysecret" not in result
    assert "[REDACTED" in result
