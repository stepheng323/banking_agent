"""Worker-backed entity extraction for interrupt switch turns."""

from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.interrupt.context import logger
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_actions import _feature_tokens
from apps.chat.src.agent.orchestrator.workflows.interrupt.switching.switch_extract_context import (
    _build_transaction_extractor_context,
)


async def _extract_interrupt_switch_entities(
    *,
    state: OrchestratorState,
    interrupt: Any,
    text: str,
    services: dict[str, Any],
    target_intent: str,
) -> tuple[dict[str, Any], set[str], str | None]:
    worker = services.get(target_intent)
    extractor = getattr(worker, "extractor", None) if worker else None
    if extractor is None or not hasattr(extractor, "extract"):
        return {}, set(), None

    context = _build_transaction_extractor_context(
        state=state,
        interrupt=interrupt,
        target_intent=target_intent,
    )
    try:
        extraction = await extractor.extract(text, smart_context=context)
    except Exception as exc:
        logger.warning(f"interrupt_switch_{target_intent}_extract_failed", error=str(exc))
        return {}, set(), None

    entities = extraction.entities.model_dump(exclude_none=True) if getattr(extraction, "entities", None) else {}
    correction = getattr(extraction, "correction", None)
    if correction and getattr(correction, "field", None) and getattr(correction, "new_value", None) is not None:
        field = correction.field.value if hasattr(correction.field, "value") else str(correction.field)
        entities[field] = correction.new_value

    features = _feature_tokens(getattr(extraction, "requested_features", None))
    acknowledgment = str(getattr(extraction, "acknowledgment", "") or "").strip() or None
    return entities, features, acknowledgment


__all__ = ["_extract_interrupt_switch_entities"]
