from decimal import Decimal

from apps.chat.src.agent.orchestrator.graph.checkpoint_serializer import OrchestratorRedisSerializer
from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def test_orchestrator_redis_serializer_preserves_decimal_task_payload_amount() -> None:
    state = OrchestratorState(
        user_id="u_checkpoint_decimal",
        phone_number="2348000000200",
        tasks={
            "direct_transfer": TaskSpec(
                id="direct_transfer",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "message": "Send 20k to mum",
                    "instruction": "Send 20k to mum",
                    "amount": Decimal("20000.0"),
                    "recipient_name": "mum",
                },
            )
        },
        waves=[["direct_transfer"]],
        pending_interrupt=PendingInterrupt(
            kind="input",
            task_ids=["direct_transfer"],
            fields_by_task={"direct_transfer": ["recipient_account", "recipient_bank_name"]},
        ),
    )

    serializer = OrchestratorRedisSerializer()
    restored = serializer.loads_typed(serializer.dumps_typed(state))

    amount = restored.tasks["direct_transfer"].payload["amount"]
    assert amount == Decimal("20000.00")
