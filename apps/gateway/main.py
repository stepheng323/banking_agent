from dotenv import load_dotenv
from fastapi import FastAPI

# Load environment variables from .env file
load_dotenv()

from apps.gateway.api.flow_webhook import router as flow_webhook_router
from apps.gateway.api.webhook import router as webhook_router

app = FastAPI(title="WhatsApp Gateway Service")
app.include_router(webhook_router)
app.include_router(flow_webhook_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
