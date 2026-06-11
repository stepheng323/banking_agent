from shared.messaging.body_blocks import render_body_blocks_telegram_html, render_body_blocks_text
from shared.messaging.intents import RequestAuth, RequestConfirmation, Say, reconstruct_intent


def test_message_body_blocks_render_mobile_spacing() -> None:
    blocks = [
        {"type": "heading", "text": "Transfers failed"},
        {
            "type": "transaction_item",
            "status": "failed",
            "title": "₦30,000 → Mom (Fatima Zahra Musa)",
            "subtitle": "Wema • 8067892221",
            "reason": "Transfer creation failed",
        },
        {"type": "text", "text": "All transactions failed."},
    ]

    rendered = render_body_blocks_text(blocks)

    assert rendered == (
        "Transfers failed\n\n"
        "✗ ₦30,000 → Mom (Fatima Zahra Musa)\n"
        "Wema • 8067892221\n"
        "Reason: Transfer creation failed\n\n"
        "All transactions failed."
    )


def test_message_body_blocks_render_telegram_safe_html() -> None:
    html = render_body_blocks_telegram_html(
        [
            {"type": "heading", "text": "*Confirm*"},
            {"type": "text", "text": "<unsafe>"},
        ]
    )

    assert "<b>Confirm</b>" in html
    assert "&lt;unsafe&gt;" in html


def test_intents_serialize_and_reconstruct_body_blocks() -> None:
    blocks = [{"type": "text", "text": "Structured body"}]

    for intent in (
        Say(text="fallback", body_blocks=blocks),
        RequestConfirmation(
            task_ids=["t1"],
            summary="fallback",
            token="tok",
            correlation_id="idem",
            body_blocks=blocks,
        ),
        RequestAuth(method="pin", task_ids=["t1"], correlation_id="idem", summary="fallback", body_blocks=blocks),
    ):
        reconstructed = reconstruct_intent(intent.to_dict())

        assert reconstructed is not None
        assert getattr(reconstructed, "body_blocks") == blocks
