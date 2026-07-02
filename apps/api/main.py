"""API entrypoint. Mounts the interface clusters from the domain design doc.

This layer is framework-agnostic: it only knows platform contracts
(shared.models), never Freqtrade/CCXT/Jesse (原则一：框架内聚).
"""

from fastapi import FastAPI

from apps.api.config import settings
from apps.api.routers import backtests, ingestion, review, risk, runs, strategies

app = FastAPI(title="AI Quant Research Platform", version="0.1.0")

app.include_router(strategies.router)
app.include_router(backtests.router)
app.include_router(runs.router)
app.include_router(risk.router)
app.include_router(review.router)
app.include_router(ingestion.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "phase-0", "env": settings.app_env}
