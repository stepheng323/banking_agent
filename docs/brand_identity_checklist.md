# Brand Identity Checklist

Use env-backed app identity as the source of truth:

- `APP_NAME`: canonical assistant name shown in replies.
- `APP_NAME_SHORT`: short name accepted in addressed greetings.
- `APP_NAME_ALIASES`: comma-separated current aliases accepted as valid names.
- `APP_LEGACY_NAMES`: comma-separated old names recognized for correction.

External surfaces must be renamed outside the chat runtime:

- Telegram BotFather bot display name and username, where applicable.
- WhatsApp Business profile display name.
- Any admin, frontend, or transcript UI label that prints the assistant speaker name.

Repo invariant: hardcoded brand names should live only as env fallback defaults in `shared/config/settings.py`.
