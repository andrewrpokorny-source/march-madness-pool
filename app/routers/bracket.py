from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Game, Team, Owner
from app.config import ROUND_ORDER

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Standard bracket order within a region (top to bottom)
SEED_ORDER = [1, 16, 8, 9, 5, 12, 4, 13, 6, 11, 3, 14, 7, 10, 2, 15]

# Maps each seed to its line position (0-15) in the bracket
SEED_BRACKET_SLOT = {
    1: 0, 16: 1, 8: 2, 9: 3, 5: 4, 12: 5, 4: 6, 13: 7,
    6: 8, 11: 9, 3: 10, 14: 11, 7: 12, 10: 13, 2: 14, 15: 15,
}

# Fixed region layout: [left-top, left-bottom, right-top, right-bottom]
REGION_LAYOUT = ["East", "South", "West", "Midwest"]

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


def _bracket_sort_key(game):
    """Compute bracket position for a game based on team seeds.

    Uses the minimum bracket slot of either team so games align
    vertically with the R64 matchups they feed from.
    """
    slots = []
    if game.team1 and game.team1.seed:
        slots.append(SEED_BRACKET_SLOT.get(game.team1.seed, 99))
    if game.team2 and game.team2.seed:
        slots.append(SEED_BRACKET_SLOT.get(game.team2.seed, 99))
    return min(slots) if slots else 99


def _sort_round_games(round_games, round_name):
    """Sort games within a round, properly interleaving TBD games.

    Known games are placed at their correct bracket positions.
    TBD games fill the remaining slots in order.
    """
    expected = {"Round of 64": 8, "Round of 32": 4, "Sweet 16": 2, "Elite 8": 1}
    num_slots = expected.get(round_name, len(round_games))

    known = []
    tbd = []
    for g in round_games:
        key = _bracket_sort_key(g)
        if key < 99:
            known.append((key, g))
        else:
            tbd.append(g)

    known.sort(key=lambda x: x[0])

    # Place known games in their slots, TBD games fill remaining
    result = [None] * num_slots
    used = set()

    for sort_key, game in known:
        # Map the sort key (0-15 range) into the slot index for this round
        # R64: 8 games, positions map to slots 0-7 (divide by 2)
        # R32: 4 games, positions map to slots 0-3 (divide by 4)
        # S16: 2 games, positions map to slots 0-1 (divide by 8)
        # E8: 1 game, slot 0
        divisor = 16 // num_slots
        slot = min(sort_key // divisor, num_slots - 1)
        # Find nearest available slot
        if result[slot] is None:
            result[slot] = game
            used.add(slot)
        else:
            # Slot taken, find nearest open
            for offset in range(1, num_slots):
                for try_slot in [slot + offset, slot - offset]:
                    if 0 <= try_slot < num_slots and result[try_slot] is None:
                        result[try_slot] = game
                        used.add(try_slot)
                        break
                else:
                    continue
                break

    # Fill remaining with TBD games
    tbd_idx = 0
    for i in range(num_slots):
        if result[i] is None and tbd_idx < len(tbd):
            result[i] = tbd[tbd_idx]
            tbd_idx += 1

    return [g for g in result if g is not None]


def _build_bracket(db: Session) -> dict:
    """Build bracket data structure from games and teams."""
    games = db.query(Game).all()
    teams = db.query(Team).all()

    # Group games by region and round
    games_by_region = {}
    first_four_games = []
    final_four_games = []
    championship_game = None

    for game in games:
        if game.round_name in ("Final Four", "Championship"):
            if game.round_name == "Championship":
                championship_game = game
            else:
                final_four_games.append(game)
        elif game.round_name == "First Four":
            first_four_games.append(game)
        elif game.region:
            if game.region not in games_by_region:
                games_by_region[game.region] = {}
            if game.round_name not in games_by_region[game.region]:
                games_by_region[game.region][game.round_name] = []
            games_by_region[game.region][game.round_name].append(game)

    # Sort games within each round by bracket position
    for region in games_by_region.values():
        for round_name in region:
            region[round_name] = _sort_round_games(region[round_name], round_name)

    # Build ordered regions dict following the fixed layout
    ordered_regions = {}
    for region_name in REGION_LAYOUT:
        if region_name in games_by_region:
            ordered_regions[region_name] = games_by_region[region_name]

    # Build team lookup for bracket slots
    team_lookup = {t.id: t for t in teams}

    return {
        "regions": ordered_regions,
        "first_four": first_four_games,
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
    # "all" means user explicitly wants all games
    if round_name == "all":
        round_name = None

    # Auto-detect round with live games if no filter specified
    auto_round = None
    if round_name is None and "round_name" not in request.query_params:
        live_game = db.query(Game).filter(Game.status == "in_progress").first()
        if live_game:
            auto_round = live_game.round_name

    active_round = round_name or auto_round
    # Sort: live first, then final, then scheduled; within each group by date
    status_order = case(
        (Game.status == "in_progress", 0),
        (Game.status == "final", 1),
        else_=2,
    )
    query = db.query(Game).order_by(status_order, Game.game_date.desc().nullslast())

    if active_round:
        query = query.filter(Game.round_name == active_round)

    games = query.all()

    return templates.TemplateResponse("games.html", {
        "request": request,
        "games": games,
        "rounds": ROUND_ORDER,
        "selected_round": active_round,
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
