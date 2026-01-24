"""Base repository with common CRUD operations."""

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.models import Base

ModelType = TypeVar("ModelType", bound=Base)  # type: ignore[type-arg]


class BaseRepository(Generic[ModelType]):
    """Base repository with common CRUD operations."""

    def __init__(self, db: AsyncSession, model: type[ModelType]):
        self.db = db
        self.model = model

    async def get_by_id(self, record_id: str) -> ModelType | None:
        """Get a record by ID."""
        result = await self.db.execute(select(self.model).filter(self.model.id == record_id))  # type: ignore[attr-defined]
        return result.scalars().first()

    async def get_all(self, skip: int = 0, limit: int = 20) -> list[ModelType]:
        """Get all records with pagination."""
        result = await self.db.execute(select(self.model).offset(skip).limit(limit))
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> ModelType:
        """Create a new record."""
        instance = self.model(**kwargs)
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def update(self, instance: ModelType, **kwargs: Any) -> ModelType:
        """Update an existing record."""
        for key, value in kwargs.items():
            setattr(instance, key, value)
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def delete(self, instance: ModelType) -> None:
        """Delete a record."""
        await self.db.delete(instance)
        await self.db.flush()

    async def get_or_create(self, defaults: dict[str, Any] | None = None, **kwargs: Any) -> tuple[ModelType, bool]:
        """Get a record or create if it doesn't exist."""
        query = select(self.model).filter_by(**kwargs)
        result = await self.db.execute(query)
        instance = result.scalars().first()

        if instance:
            return instance, False

        if defaults:
            kwargs.update(defaults)
        instance = await self.create(**kwargs)
        return instance, True
