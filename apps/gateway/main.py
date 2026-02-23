from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.gateway.api.webhooks import flows_router, message_router, mono_router, telegram_router
from shared.utils.logging import configure_logger

load_dotenv()

configure_logger()

app = FastAPI(title="Gateway Service")
app.include_router(message_router)
app.include_router(mono_router)
app.include_router(flows_router)
app.include_router(telegram_router)

# Serve static files for Telegram Mini App
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
