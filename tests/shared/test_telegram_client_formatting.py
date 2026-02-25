from shared.clients.telegram.client import _format_telegram_html


def test_telegram_html_formatter_escapes_html_and_formats_markdown() -> None:
    rendered = _format_telegram_html("*Bold* _italics_ `code` <tag>")

    assert "<b>Bold</b>" in rendered
    assert "<i>italics</i>" in rendered
    assert "<code>code</code>" in rendered
    assert "&lt;tag&gt;" in rendered


def test_telegram_html_formatter_does_not_break_plain_text() -> None:
    rendered = _format_telegram_html("Which account would you like to use?")
    assert rendered == "Which account would you like to use?"
