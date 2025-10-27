"""Repository for User model."""

from typing import Optional, List
from sqlalchemy.orm import Session
from shared.database.models import User
from shared.models.user import UserCreate
from shared.repositories.base import BaseRepository
from datetime import datetime
import uuid


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

        user = User(
            phone_number=user_data.phone_number,
            full_name=user_data.full_name,
            email=user_data.email,
            onboarding_status=user_data.onboarding_status,
            extra_data=user_data.extra_data or {},
        )

        self.db.add(user)
        self.db.flush()
        return user

    def update_last_active(self, user_id: str) -> None:
        """Update user's last active timestamp (doesn't commit)."""
        user = self.get_by_id(user_id)
        if user:
            user.last_active = datetime.utcnow()

    def mark_verified(self, user_id: str) -> User:
        """Mark user as verified (doesn't commit)."""
        user = self.get_by_id(user_id)
        if user:
            user.is_verified = True
        return user

    def get_registered_count(self) -> int:
        """Get count of registered users."""
        return self.db.query(User).count()
