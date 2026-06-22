"""Model selection for the chat runtime."""

from dataclasses import dataclass

from langchain_openai import ChatOpenAI

from shared.observability.llm_http import build_llm_http_async_client
from shared.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ChatRoleModels:
    """LLMs selected for the chat runtime roles."""

    planner_llm: ChatOpenAI
    query_llm: ChatOpenAI
    semantic_router_llm: ChatOpenAI
    interrupt_llm: ChatOpenAI
    extractor_llm: ChatOpenAI
    query_model: str
    semantic_router_model: str
    interrupt_router_model: str
    extractor_model: str


def resolve_role_model(*, role: str, configured_model: str, planner_model: str, app_env: str) -> str:
    """Resolve role model from env and log the selected model."""
    model = configured_model.strip()
    if not model:
        model = planner_model

    logger.info(
        f"{role}_model_selected",
        app_env=app_env,
        model=model,
    )
    return model



def build_chat_role_models(
    *,
    planner_model: str,
    query_model: str,
    semantic_router_model: str,
    interrupt_router_model: str,
    extractor_model: str,
    app_env: str,
) -> ChatRoleModels:
    """Build role-specific LLM clients for the chat runtime."""
    logger.info("planner_model_selected", app_env=app_env, model=planner_model)

    resolved_query_model = resolve_role_model(
        role="query",
        configured_model=query_model,
        planner_model=planner_model,
        app_env=app_env,
    )
    resolved_semantic_router_model = resolve_role_model(
        role="semantic_router",
        configured_model=semantic_router_model,
        planner_model=planner_model,
        app_env=app_env,
    )
    resolved_interrupt_router_model = resolve_role_model(
        role="interrupt_router",
        configured_model=interrupt_router_model,
        planner_model=planner_model,
        app_env=app_env,
    )
    resolved_extractor_model = resolve_role_model(
        role="extractor",
        configured_model=extractor_model,
        planner_model=planner_model,
        app_env=app_env,
    )

    logger.info(
        "chat_worker_role_models_resolved",
        planner_model=planner_model,
        query_model=resolved_query_model,
        semantic_router_model=resolved_semantic_router_model,
        interrupt_router_model=resolved_interrupt_router_model,
        extractor_model=resolved_extractor_model,
    )
    http_async_client = build_llm_http_async_client()

    return ChatRoleModels(
        planner_llm=ChatOpenAI(
            model=planner_model,
            temperature=0,
            timeout=30.0,
            max_retries=1,
            http_async_client=http_async_client,
            include_response_headers=True,
        ),
        query_llm=ChatOpenAI(
            model=resolved_query_model,
            temperature=0,
            timeout=30.0,
            max_retries=1,
            http_async_client=http_async_client,
            include_response_headers=True,
        ),
        semantic_router_llm=ChatOpenAI(
            model=resolved_semantic_router_model,
            temperature=0,
            timeout=15.0,
            max_retries=1,
            http_async_client=http_async_client,
            include_response_headers=True,
        ),
        interrupt_llm=ChatOpenAI(
            model=resolved_interrupt_router_model,
            temperature=0,
            timeout=15.0,
            max_retries=1,
            http_async_client=http_async_client,
            include_response_headers=True,
        ),
        extractor_llm=ChatOpenAI(
            model=resolved_extractor_model,
            temperature=0,
            timeout=20.0,
            max_retries=1,
            http_async_client=http_async_client,
            include_response_headers=True,
        ),
        query_model=resolved_query_model,
        semantic_router_model=resolved_semantic_router_model,
        interrupt_router_model=resolved_interrupt_router_model,
        extractor_model=resolved_extractor_model,
    )
