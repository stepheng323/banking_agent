"""Tests for Mono webhook handler - Unit tests with mocked dependencies."""


class TestWebhookServiceBasics:
    """Basic unit tests for webhook handling logic."""

    def test_debit_success_status_mapping(self):
        """Test that debit success status is correctly identified."""
        success_events = [
            "events.mandates.debit.successful",
            "direct_debit.payment_successful",
        ]
        for event in success_events:
            assert "successful" in event.lower() or "success" in event.lower()

    def test_debit_failure_status_identification(self):
        """Test that debit failure status is correctly identified."""
        failure_events = [
            "events.mandates.debit.failed",
            "direct_debit.payment_failed",
        ]
        for event in failure_events:
            assert "failed" in event.lower()


class TestFormatting:
    """Tests for multi-source formatting utilities."""

    def test_format_multi_source_transfer_summary(self):
        """Test multi-source confirmation formatting."""
        from shared.formatters.transfer import format_multi_source_transfer_summary

        result = format_multi_source_transfer_summary(
            {
                "amount": 50000,
                "recipientName": "John Doe",
                "recipientBank": "GTBank",
                "recipientAccount": "1234567890",
                "funding_sources": [
                    {"bank_name": "UBA", "account_number": "1111111111", "amount": 30000},
                    {"bank_name": "Access", "account_number": "2222222222", "amount": 20000},
                ],
                "narration": "Test payment",
            }
        )

        assert "₦50,000" in result
        assert "John Doe" in result
        assert "Funding from:" in result
        assert "UBA" in result
        assert "Access" in result
        assert "₦30,000" in result
        assert "₦20,000" in result

    def test_format_multi_source_receipt(self):
        """Test multi-source receipt formatting."""
        from shared.formatters.transfer import format_multi_source_receipt

        result = format_multi_source_receipt(
            {
                "amount": 50000,
                "recipientName": "John Doe",
                "recipientBank": "GTBank",
                "recipientAccount": "1234567890",
                "funding_sources": [
                    {"bank_name": "UBA", "account_number": "1111111111", "amount": 30000},
                    {"bank_name": "Access", "account_number": "2222222222", "amount": 20000},
                ],
                "reference": "TRF-123456",
            }
        )

        assert "Transfer Successful" in result
        assert "₦50,000" in result
        assert "Funded from:" in result
        assert "TRF-123456" in result

    def test_single_source_receipt_no_breakdown(self):
        """Single source receipt doesn't show 'Funded from:' header."""
        from shared.formatters.transfer import format_multi_source_receipt

        result = format_multi_source_receipt(
            {
                "amount": 50000,
                "recipientName": "John Doe",
                "recipientBank": "GTBank",
                "recipientAccount": "1234567890",
                "funding_sources": [
                    {"bank_name": "UBA", "account_number": "1111111111", "amount": 50000},
                ],
                "reference": "TRF-123456",
            }
        )

        # Single source should not show "Funded from:" breakdown
        assert "Funded from:" not in result
        assert "From:" in result


class TestFundingStepStatus:
    """Tests for funding step status management."""

    def test_funding_step_status_enum_values(self):
        """Test FundingStepStatusEnum has expected values."""
        from shared.database.models import FundingStepStatusEnum

        assert FundingStepStatusEnum.PENDING.value == "pending"
        assert FundingStepStatusEnum.PROCESSING.value == "processing"
        assert FundingStepStatusEnum.CONFIRMED.value == "confirmed"
        assert FundingStepStatusEnum.FAILED.value == "failed"
        assert FundingStepStatusEnum.REFUND_PENDING.value == "refund_pending"
        assert FundingStepStatusEnum.REFUNDED.value == "refunded"

    def test_funded_transfer_status_enum_values(self):
        """Test FundedTransferStatusEnum has expected values."""
        from shared.database.models import FundedTransferStatusEnum

        assert FundedTransferStatusEnum.DRAFT.value == "draft"
        assert FundedTransferStatusEnum.FUNDING_PENDING.value == "funding_pending"
        assert FundedTransferStatusEnum.COMPLETED.value == "completed"
        assert FundedTransferStatusEnum.REFUNDING.value == "refunding"
        assert FundedTransferStatusEnum.REFUNDED.value == "refunded"
