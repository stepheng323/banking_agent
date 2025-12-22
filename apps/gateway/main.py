from apps.gateway.api.webhooks import mono_router, whatsapp_router, flows_router
from dotenv import load_dotenv
from fastapi import FastAPI
from shared.utils.logging import configure_logger

load_dotenv()

configure_logger()

app = FastAPI(title="WhatsApp Gateway Service")
app.include_router(whatsapp_router)
app.include_router(mono_router)
app.include_router(flows_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


