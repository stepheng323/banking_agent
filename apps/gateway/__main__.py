import uvicorn
from bot.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "bot.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
