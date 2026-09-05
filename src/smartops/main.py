from fastapi import FastAPI

app = FastAPI(title="SmartOps", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "smartops"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "SmartOps",
        "status": "foundation-ready",
        "next": "Build workflow state model and browser extraction engine",
    }
