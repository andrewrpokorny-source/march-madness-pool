import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.espn import fetch_tournament_scores

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def admin_panel(request: Request):
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "page_title": "Admin",
    })


@router.post("/sync")
async def sync_scores(db: Session = Depends(get_db)):
    stats = await fetch_tournament_scores(db)
    return stats
