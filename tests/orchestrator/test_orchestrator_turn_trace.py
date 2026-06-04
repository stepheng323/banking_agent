from apps.chat.src.agent.orchestrator.graph.turn_trace import summarize_orchestrator_turn_trace
from apps.chat.src.agent.orchestrator.models.domain import PendingInterrupt, TaskSpec, TaskStage


def test_orchestrator_turn_trace_summary_extracts_route_execution_and_interrupt_fields() -> None:
    interrupt = PendingInterrupt(kind="confirmation", task_ids=["task_transfer"], prompt="Confirm?")
    task = TaskSpec(
        id="task_transfer",
        type="transfer",
        stage=TaskStage.AWAITING_CONFIRMATION,
        payload={"amount": "1000"},
    )

    summary = summarize_orchestrator_turn_trace(
        final_state={
            "routing_owner": "planner",
            "routing_decision": "transfer",
            "routing_target_domain": "transfer",
            "routing_mode": "new",
            "route_source": "planner",
            "planner_used": True,
            "waves": [["task_transfer"]],
            "pending_interrupt": interrupt,
            "tasks": {"task_transfer": task},
        },
        path_label="planner_path",
        semantic_path_shape="planner",
        total_duration_ms=12.34567,
        progress_count=2,
    )

    assert summary == {
        "path_label": "planner_path",
        "semantic_path_shape": "planner",
        "gate_match": "transfer",
        "planner_used": True,
        "execution_wave_count": 1,
        "interrupt_status": "confirmation",
        "task_executors": ["transfer"],
        "routing_owner": "planner",
        "routing_decision": "transfer",
        "routing_target_domain": "transfer",
        "routing_mode": "new",
        "route_source": "planner",
        "progress_count": 2,
        "total_duration_ms": 12.346,
    }
