from banking.security.authorization_context import authorized_idempotency_keys, is_task_authorized_by_pin


def test_authorized_idempotency_keys_extracts_primary_and_batch_keys() -> None:
    keys = authorized_idempotency_keys(
        {
            "authorization_context": {
                "idempotency_key": "idem-primary",
                "authorized_task_idempotency_keys": ["idem-primary", "idem-second", ""],
            }
        }
    )

    assert keys == {"idem-primary", "idem-second"}


def test_task_authorized_by_pin_requires_exact_idempotency_key() -> None:
    context = {
        "authorization_context": {
            "idempotency_key": "idem-A",
            "authorized_task_idempotency_keys": ["idem-A"],
        }
    }

    assert is_task_authorized_by_pin(context=context, idempotency_key="idem-A", pin_verified=True) is True
    assert is_task_authorized_by_pin(context=context, idempotency_key="idem-B", pin_verified=True) is False
    assert is_task_authorized_by_pin(context=context, idempotency_key="idem-A", pin_verified=False) is False
