"""Embedding service for FAQ vector search."""

from openai import AsyncOpenAI

from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.utils.logging import get_logger

logger = get_logger(__name__)

# OpenAI embedding model - text-embedding-3-small is fast and cheap
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536  # Default for text-embedding-3-small


class EmbeddingService:
    """Generate embeddings using OpenAI API."""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def get_embedding(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            List of floats representing the embedding vector
        """
        try:
            response = await self.client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text,
            )
            emit_operational_event(
                "llm_embedding_generated",
                severity="info",
                domain="llm",
                details={"model": EMBEDDING_MODEL, "input_count": 1, "input_chars": len(text)},
            )
            return response.data[0].embedding
        except Exception as e:
            emit_operational_event(
                "llm_embedding_failed",
                severity="warning",
                domain="llm",
                details={"model": EMBEDDING_MODEL, "input_count": 1, "error_type": type(e).__name__},
            )
            logger.error(f"Failed to generate embedding: {e}")
            raise

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in a single API call.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        try:
            response = await self.client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            emit_operational_event(
                "llm_embedding_generated",
                severity="info",
                domain="llm",
                details={"model": EMBEDDING_MODEL, "input_count": len(texts), "input_chars": sum(map(len, texts))},
            )
            # Return embeddings in the same order as input
            return [item.embedding for item in response.data]
        except Exception as e:
            emit_operational_event(
                "llm_embedding_failed",
                severity="warning",
                domain="llm",
                details={"model": EMBEDDING_MODEL, "input_count": len(texts), "error_type": type(e).__name__},
            )
            logger.error(f"Failed to generate embeddings: {e}")
            raise

    def get_embedding_sync(self, text: str) -> list[float]:
        """
        Synchronous version for use in seed scripts.

        Args:
            text: Text to embed

        Returns:
            List of floats representing the embedding vector
        """
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)

        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise

    def get_embeddings_sync(self, texts: list[str]) -> list[list[float]]:
        """
        Synchronous batch version for use in seed scripts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        from openai import OpenAI

        if not texts:
            return []

        client = OpenAI(api_key=settings.openai_api_key)

        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise
