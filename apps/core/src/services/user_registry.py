from typing import Optional
from shared.models.user import User
from shared.database.connection import get_db_session
from sqlalchemy.orm import Session


class UserRegistry:

    def __init__(self):
        self.db: Optional[Session] = None

    def _get_db(self) -> Session:
        if self.db is None:
            self.db = get_db_session()
        return self.db

    def is_registered(self, user_id: str) -> bool:
        db = self._get_db()
        user = db.query(User).filter(User.phone_number == user_id).first()
        return user is not None

    def register_user(
        self,
        user_id: str,
        details: Optional[dict] = None,
        whatsapp_id: Optional[str] = None,
    ) -> User:
        db = self._get_db()

        existing_user = db.query(User).filter(User.phone_number == user_id).first()
        if existing_user:
            return existing_user

        user = User(
            phone_number=user_id,
            whatsapp_id=whatsapp_id,
            full_name=details.get("name") if details else None,
            email=details.get("email") if details else None,
            is_active=True,
            is_verified=False,
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        print(f"   ✅ User registered: {user_id}")
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        db = self._get_db()
        return db.query(User).filter(User.phone_number == user_id).first()

    def update_user_metadata(self, user_id: str, extra_data: dict) -> None:
        db = self._get_db()
        user = db.query(User).filter(User.phone_number == user_id).first()
        if user:
            user.extra_data.update(extra_data)
            db.commit()

    def get_registered_count(self) -> int:
        db = self._get_db()
        return db.query(User).count()

    def close(self):
        if self.db:
            self.db.close()
            self.db = None
