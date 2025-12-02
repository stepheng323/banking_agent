"""Repository for User model."""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from shared.database.models import User
from shared.models.user import UserCreate, UserUpdate
from shared.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User operations."""

    def __init__(self, db: Session):
        super().__init__(db, User)

    def get_by_phone(self, phone_number: str) -> Optional[User]:
        """Get user by phone number."""
        return self.db.query(User).filter(User.phone_number == phone_number).first()

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        return self.db.query(User).filter(User.email == email).first()

    def get_by_whatsapp_id(self, whatsapp_id: str) -> Optional[User]:
        """Get user by WhatsApp ID."""
        return self.db.query(User).filter(User.whatsapp_id == whatsapp_id).first()

    def is_registered(self, phone_number: str) -> bool:
        """Check if a user is registered."""
        user = self.get_by_phone(phone_number)
        return user is not None

    def register_user(self, user_data: UserCreate) -> User:
        """Register a new user."""
        existing_user = self.get_by_phone(user_data.phone_number)
        if existing_user:
            return existing_user

        user_dict = user_data.model_dump(exclude_unset=True)
        if "extra_data" not in user_dict or user_dict["extra_data"] is None:
            user_dict["extra_data"] = {}

        user = User(**user_dict)
        self.db.add(user)
        self.db.flush()
        return user

    def update_user(self, user_id: str, user_data: UserUpdate) -> User:
        """Update an existing user."""
        user = self.get_by_id(user_id)
        if not user:
            raise ValueError(f"User with ID {user_id} not found")

        for field, value in user_data.model_dump(exclude_unset=True).items():
            setattr(user, field, value)

        self.db.flush()
        return user

    def update_last_active(self, user_id: str) -> None:
        """Update user's last active timestamp (doesn't commit)."""
        user = self.get_by_id(user_id)
        if user is not None:
            setattr(user, "last_active", datetime.utcnow())

    def mark_verified(self, user_id: str) -> User:
        """Mark user as verified (doesn't commit)."""
        user = self.get_by_id(user_id)
        if user:
            user.is_verified = True
        return user

    def get_registered_count(self) -> int:
        """Get count of registered users."""
        return self.db.query(User).count()

    def get_accounts_by_phone(self, phone_number: str) -> list:
        """Get user accounts by phone number."""
        user = self.get_by_phone(phone_number)
        if user:
            return user.accounts
        return []
