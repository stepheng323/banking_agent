from fastapi import FastAPI

app = FastAPI(title="Core Banking Service")

@app.get("/")
async def root():
    return {
        "service": "Core Banking Service",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}
