"""FastAPI entry point: JSON API, dashboard pages, and the background scheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if get_settings().enable_scheduler:
        from app.scheduler import start_scheduler
        scheduler = start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="SAMVEDNA API",
    description="Situational Awareness & Monitoring of Vulnerabilities, Events, Disasters & News Analytics: "
                "open-source hazard and news monitoring for southern and western India.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"states": get_settings().states})


@app.get("/about", response_class=HTMLResponse, include_in_schema=False)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"cfg": get_settings()})
