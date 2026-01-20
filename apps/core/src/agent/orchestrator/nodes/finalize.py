from apps.core.src.agent.orchestrator.models.domain import TaskStage
from apps.core.src.agent.orchestrator.state import OrchestratorState


async def finalize(state: OrchestratorState) -> dict:
    """Final Step. Generate response."""
    results = []
    outbox = []

    for tid, task in state.tasks.items():
        if task.stage == TaskStage.COMPLETED:
            amount = task.payload.get("amount", "unknown")
            results.append(f"✅ Executed transfer of {amount}")

            if "receipt" in task.payload:
                outbox.append(
                    {
                        "type": "show_receipt",
                        "task_id": tid,
                        "receipt": task.payload["receipt"],
                        "caption": f"Receipt for Transfer of {amount}",
                    }
                )

        elif task.stage == TaskStage.FAILED:
            results.append(f"❌ Failed: {task.payload.get('error')}")

    final_text = "\n".join(results) if results else "I'm done processing."

    return {
        "final_response": final_text,
        "outbox": outbox,
        "waves": [],  # Clear waves so next turn triggers Planner
        "current_wave_index": 0,
        "pin_verified": False,  # Security: Reset PIN verification status
        "last_callback": None,  # Security: Clear stale callback data
    }
