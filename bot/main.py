from fastapi import FastAPI
from bot.api.webhook import router as webhook_router

app = FastAPI(title="WhatsApp Bot")
app.include_router(webhook_router)
