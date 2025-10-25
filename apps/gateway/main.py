from fastapi import FastAPI
from gateway.api.webhook import router as webhook_router

app = FastAPI(title="WhatsApp Gateway Service")
app.include_router(webhook_router)
