"""Temporary API bootstrap entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="AI Quant Research Platform")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "bootstrap"}

