from shared.utils.network_utils import (
    format_network_display_name,
    normalize_network_name,
    normalize_nigerian_phone,
    normalize_phone,
    resolve_network_from_phone,
)


def test_normalize_nigerian_phone_handles_local_country_and_spaced_inputs() -> None:
    assert normalize_nigerian_phone("816 251 1023") == "08162511023"
    assert normalize_nigerian_phone("8162511023") == "08162511023"
    assert normalize_nigerian_phone("+2348162511023") == "08162511023"
    assert normalize_nigerian_phone("2348162511023") == "08162511023"
    assert normalize_nigerian_phone("08162511023") == "08162511023"


def test_normalize_nigerian_phone_returns_none_for_invalid_shape() -> None:
    assert normalize_nigerian_phone("2511023") is None


def test_normalize_phone_falls_back_to_digits_when_not_normalizable() -> None:
    assert normalize_phone("0816-foo") == "0816"


def test_resolve_network_from_phone_supports_spaced_and_no_leading_zero() -> None:
    assert resolve_network_from_phone("816 251 1023") == "MTN"
    assert resolve_network_from_phone("8162511023") == "MTN"


def test_normalize_network_name_aliases() -> None:
    assert normalize_network_name("Airtel") == "AIRTEL"
    assert normalize_network_name("etisalat") == "9MOBILE"


def test_format_network_display_name_uses_human_facing_labels() -> None:
    assert format_network_display_name("MTN") == "MTN"
    assert format_network_display_name("AIRTEL") == "Airtel"
    assert format_network_display_name("glo") == "Glo"
    assert format_network_display_name("9MOBILE") == "9mobile"
