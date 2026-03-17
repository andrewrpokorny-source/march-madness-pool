from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Game, Team, Owner
from app.config import ROUND_ORDER

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Standard bracket order within a region (top to bottom)
SEED_ORDER = [1, 16, 8, 9, 5, 12, 4, 13, 6, 11, 3, 14, 7, 10, 2, 15]

OWNER_COLORS = {
    "Esther": "#ef4444",
    "Jim": "#3b82f6",
    "Posey": "#10b981",
    "Matthew": "#f59e0b",
    "Brittany": "#8b5cf6",
    "Andrew": "#ec4899",
    "Michael": "#06b6d4",
    "Brenda": "#84cc16",
}


def _build_bracket(db: Session) -> dict:
    """Build bracket data structure from games and teams."""
    games = db.query(Game).all()
    teams = db.query(Team).all()

    # Group games by region and round
    games_by_region = {}
    final_four_games = []
    championship_game = None

    for game in games:
        if game.round_name in ("Final Four", "Championship"):
            if game.round_name == "Championship":
                championship_game = game
            else:
                final_four_games.append(game)
        elif game.region:
            if game.region not in games_by_region:
                games_by_region[game.region] = {}
            if game.round_name not in games_by_region[game.region]:
                games_by_region[game.region][game.round_name] = []
            games_by_region[game.region][game.round_name].append(game)

    # Build team lookup for bracket slots
    team_lookup = {t.id: t for t in teams}

    return {
        "regions": games_by_region,
        "final_four": final_four_games,
        "championship": championship_game,
        "teams": teams,
        "team_lookup": team_lookup,
    }


@router.get("/bracket", response_class=HTMLResponse)
async def bracket_view(request: Request, db: Session = Depends(get_db)):
    bracket = _build_bracket(db)
    owners = db.query(Owner).all()
    teams = db.query(Team).all()

    # Build a flat list of all games for the bracket
    all_games = db.query(Game).order_by(Game.game_date.asc().nullslast()).all()

    # Group games by round for a round-by-round bracket view
    games_by_round = {}
    for game in all_games:
        if game.round_name not in games_by_round:
            games_by_round[game.round_name] = []
        games_by_round[game.round_name].append(game)

    return templates.TemplateResponse("bracket.html", {
        "request": request,
        "page_title": "Bracket",
        "bracket": bracket,
        "games_by_round": games_by_round,
        "rounds": ROUND_ORDER,
        "owner_colors": OWNER_COLORS,
        "owners": owners,
    })


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
