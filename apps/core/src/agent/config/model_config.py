"""Centralized LLM model configuration for cost optimization."""

from dataclasses import dataclass
from typing import Literal

ModelType = Literal[
    "classification",
    "extraction",
    "planning",
    "conversation",
    "vision",
]


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a specific model."""

    name: str
    temperature: float = 0.0
    seed: int | None = 42


MODELS: dict[ModelType, ModelConfig] = {
    "classification": ModelConfig(name="gpt-4o-mini", temperature=0.0),
    "extraction": ModelConfig(name="gpt-4o-mini", temperature=0.0, seed=42),
    "planning": ModelConfig(name="gpt-4o-mini", temperature=0.0),
    "conversation": ModelConfig(name="gpt-4o-mini", temperature=0.7),
    "vision": ModelConfig(name="gpt-4o-mini", temperature=0.0),
}


def get_model_config(task_type: ModelType) -> ModelConfig:
    """Get model configuration for a task type."""
    return MODELS.get(task_type, MODELS["extraction"])


def get_model_name(task_type: ModelType) -> str:
    """Get just the model name for a task type."""
    return get_model_config(task_type).name
