from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.scoring import get_leaderboard, get_owner_detail

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    leaderboard = get_leaderboard(db)
    return templates.TemplateResponse("leaderboard.html", {
        "request": request,
        "leaderboard": leaderboard,
        "page_title": "Leaderboard",
    })


@router.get("/owner/{owner_id}", response_class=HTMLResponse)
async def owner_detail(request: Request, owner_id: int, db: Session = Depends(get_db)):
    detail = get_owner_detail(db, owner_id)
    if not detail:
        return HTMLResponse("Owner not found", status_code=404)
    return templates.TemplateResponse("owner_detail.html", {
        "request": request,
        "detail": detail,
        "page_title": detail["owner"].name,
    })
