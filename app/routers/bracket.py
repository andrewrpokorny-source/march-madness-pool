from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Game, Team
from app.config import ROUND_ORDER

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/games", response_class=HTMLResponse)
async def games(request: Request, round_name: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Game).order_by(Game.game_date.desc().nullslast())

    if round_name:
        query = query.filter(Game.round_name == round_name)

    games = query.all()

    return templates.TemplateResponse("games.html", {
        "request": request,
        "games": games,
        "rounds": ROUND_ORDER,
        "selected_round": round_name,
        "page_title": "Games",
    })


@router.get("/teams", response_class=HTMLResponse)
async def teams(request: Request, db: Session = Depends(get_db)):
    teams = db.query(Team).order_by(Team.seed, Team.name).all()
    return templates.TemplateResponse("teams.html", {
        "request": request,
        "teams": teams,
        "page_title": "All Teams",
    })
