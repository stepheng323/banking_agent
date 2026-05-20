from apps.chat.src.agent.executors.airtime import AirtimeExecutor as AppAirtimeExecutor
from apps.chat.src.agent.executors.data import DataExecutor as AppDataExecutor
from apps.chat.src.agent.executors.payout import PayoutExecutor as AppPayoutExecutor
from apps.chat.src.agent.executors.transfer import TransferExecutor as AppTransferExecutor
from shared.transaction_runtime.executors.airtime import AirtimeExecutor
from shared.transaction_runtime.executors.data import DataExecutor
from shared.transaction_runtime.executors.payout import PayoutExecutor
from shared.transaction_runtime.executors.transfer import TransferExecutor


def test_legacy_app_executor_imports_reexport_shared_runtime_classes() -> None:
    assert AppAirtimeExecutor is AirtimeExecutor
    assert AppDataExecutor is DataExecutor
    assert AppPayoutExecutor is PayoutExecutor
    assert AppTransferExecutor is TransferExecutor
