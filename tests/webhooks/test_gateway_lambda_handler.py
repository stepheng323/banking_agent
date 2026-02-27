from apps.gateway.lambda_handler import handler


def test_gateway_lambda_handler_is_callable() -> None:
    assert callable(handler)
