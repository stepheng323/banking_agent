from shared.clients.providers.mono.mock_data import get_mock_banks
from shared.utils.bank_aliases import find_matching_bank_name


def test_mono_mock_includes_high_confidence_live_institution_banks() -> None:
    banks = {bank["name"]: bank["code"] for bank in get_mock_banks()}

    assert banks["Citibank Nigeria"] == "023"
    assert banks["Jaiz Bank"] == "301"
    assert banks["Lotus Bank"] == "303"
    assert banks["Globus Bank"] == "103"
    assert banks["SunTrust Bank"] == "100"
    assert banks["Parallex Bank"] == "104"
    assert banks["Premium Trust Bank"] == "105"
    assert banks["Titan Trust Bank"] == "102"
    assert banks["Optimus Bank"] == "107"
    assert banks["Rand Merchant Bank"] == "502"
    assert banks["TAJ Bank"] == "302"
    assert banks["AltBank"] == "116"
    assert banks["ALAT by WEMA"] == "035"
    assert banks["FCMB"] == "214"


def test_new_mono_mock_banks_can_be_found_by_common_chat_names() -> None:
    bank_names = [bank["name"] for bank in get_mock_banks()]

    assert find_matching_bank_name("rmb", bank_names) == "Rand Merchant Bank"
    assert find_matching_bank_name("citi", bank_names) == "Citibank Nigeria"
    assert find_matching_bank_name("alt bank", bank_names) == "AltBank"
    assert find_matching_bank_name("titan", bank_names) == "Titan Trust Bank"
    assert find_matching_bank_name("premium trust", bank_names) == "Premium Trust Bank"
