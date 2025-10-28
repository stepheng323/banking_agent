"""Base repository with common CRUD operations."""

from typing import Generic, TypeVar, Type, Optional, List
from sqlalchemy.orm import Session
from shared.database.models import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Base repository with common CRUD operations."""

    def __init__(self, db: Session, model: Type[ModelType]):
        self.db = db
        self.model = model

    def get_by_id(self, id: str) -> Optional[ModelType]:
        """Get a record by ID."""
        return self.db.query(self.model).filter(self.model.id == id).first()

    def get_all(self, skip: int = 0, limit: int = 100) -> List[ModelType]:
        """Get all records with pagination."""
        return self.db.query(self.model).offset(skip).limit(limit).all()

    def create(self, **kwargs) -> ModelType:
        """Create a new record (doesn't commit - handled by UnitOfWork)."""
        instance = self.model(**kwargs)
        self.db.add(instance)
        self.db.flush()  # Get ID but don't commit
        return instance

    def update(self, instance: ModelType, **kwargs) -> ModelType:
        """Update an existing record (doesn't commit - handled by UnitOfWork)."""
        for key, value in kwargs.items():
            setattr(instance, key, value)
        return instance

    def delete(self, instance: ModelType) -> None:
        """Delete a record (doesn't commit - handled by UnitOfWork)."""
        self.db.delete(instance)

    def get_or_create(self, defaults: dict = None, **kwargs) -> tuple[ModelType, bool]:
        """Get a record or create if it doesn't exist."""
        instance = self.db.query(self.model).filter_by(**kwargs).first()
        if instance:
            return instance, False

        if defaults:
            kwargs.update(defaults)
        instance = self.create(**kwargs)
        return instance, True
