from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.mixed_capability_stages import (
    analyze_mixed_supported_unsupported,
    mixed_policy_notice,
)


def test_mixed_capability_extracts_supported_transfer_clause() -> None:
    match = analyze_mixed_supported_unsupported("send 5k to Ada and buy bitcoin for me")

    assert match is not None
    assert match.is_ambiguous is False
    assert match.supported[0].domain == "transfer"
    assert match.supported[0].text == "send 5k to Ada"
    assert match.unsupported[0].key == "investments"


def test_mixed_capability_works_with_unsupported_clause_first() -> None:
    match = analyze_mixed_supported_unsupported("buy bitcoin for me then send 5k to Ada")

    assert match is not None
    assert match.is_ambiguous is False
    assert match.supported[0].domain == "transfer"
    assert match.supported[0].text == "send 5k to Ada"
    assert match.unsupported[0].key == "investments"


def test_mixed_capability_ignores_same_clause_unsupported_account_language() -> None:
    assert analyze_mixed_supported_unsupported("buy bitcoin with my Access account") is None


def test_mixed_capability_marks_multiple_supported_clauses_ambiguous() -> None:
    match = analyze_mixed_supported_unsupported("send 5k to Ada and what is my balance and buy bitcoin")

    assert match is not None
    assert match.is_ambiguous is True
    assert [item.domain for item in match.supported] == ["transfer", "account"]
    assert match.unsupported[0].key == "investments"


def test_mixed_policy_notice_renders_in_locale() -> None:
    match = analyze_mixed_supported_unsupported("send 5k to Ada and buy bitcoin")
    assert match is not None

    assert mixed_policy_notice(match, locale="pcm").startswith("I fit help with money transfer.")


def test_mixed_policy_notice_uses_localized_unsupported_label() -> None:
    match = analyze_mixed_supported_unsupported("send 5k to Ada and ra bitcoin")
    assert match is not None

    assert mixed_policy_notice(match, locale="yo") == (
        "Mo le ran ọ lọwọ pẹlu transfer owo. Mi o le ran ọ lọwọ pẹlu idoko owo tabi crypto nibi."
    )
