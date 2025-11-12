from apps.gateway.api.webhook import router as webhook_router
from apps.gateway.api.flows import router as flow_webhook_router
from dotenv import load_dotenv
from fastapi import FastAPI

# Load environment variables from .env file
load_dotenv()


app = FastAPI(title="WhatsApp Gateway Service")
app.include_router(webhook_router)
app.include_router(flow_webhook_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
