"""ESPN API integration for NCAA Tournament scores."""

import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.models import Team, Game
from app.config import ROUND_ORDER

logger = logging.getLogger(__name__)

SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
)

# ESPN uses group 100 for NCAA Tournament
TOURNAMENT_PARAMS = {"groups": "100", "limit": "100"}

# Map ESPN round names/types to our round names
ESPN_ROUND_MAP = {
    1: "First Four",
    2: "Round of 64",
    3: "Round of 32",
    4: "Sweet 16",
    5: "Elite 8",
    6: "Final Four",
    7: "Championship",
}


def _normalize_name(name: str) -> str:
    """Normalize team name for matching."""
    replacements = {
        "State": "St",
        "Saint": "St",
        "St.": "St",
        "North Carolina": "UNC",
        "Brigham Young": "BYU",
        "Connecticut": "UConn",
        "Ohio State": "OSU",
        "Southern California": "USC",
        "University of California": "Cal",
        "Northern Iowa": "N Iowa",
        "North Dakota State": "N Dakota St",
        "Prairie View A&M": "Prairie View",
        "Long Island University": "LIU",
    }
    normalized = name.strip()
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized.lower().strip()


def _match_team(espn_name: str, espn_id: str, teams: list[Team]) -> Team | None:
    """Try to match an ESPN team to one of our drafted teams."""
    # First try by espn_id if already set
    for team in teams:
        if team.espn_id == espn_id:
            return team

    # Then try name matching
    normalized_espn = _normalize_name(espn_name)
    for team in teams:
        # Try exact normalized match
        if _normalize_name(team.name) == normalized_espn:
            return team
        # Try if one contains the other
        if _normalize_name(team.name) in normalized_espn or normalized_espn in _normalize_name(team.name):
            return team
        # For play-in teams, check if either name matches
        if team.playin_label:
            for part in team.playin_label.split("/"):
                if _normalize_name(part.strip()) == normalized_espn:
                    return team
                if _normalize_name(part.strip()) in normalized_espn:
                    return team

    return None


async def fetch_tournament_scores(db: Session) -> dict:
    """Fetch latest tournament scores from ESPN and update database."""
    all_teams = db.query(Team).all()
    stats = {"games_updated": 0, "games_created": 0, "errors": []}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Fetch current tournament scoreboard
            resp = await client.get(SCOREBOARD_URL, params=TOURNAMENT_PARAMS)
            resp.raise_for_status()
            data = resp.json()

            events = data.get("events", [])
            logger.info(f"ESPN returned {len(events)} tournament events")

            for event in events:
                try:
                    _process_event(event, all_teams, db, stats)
                except Exception as e:
                    logger.error(f"Error processing event: {e}")
                    stats["errors"].append(str(e))

        db.commit()
    except httpx.HTTPError as e:
        logger.error(f"ESPN API error: {e}")
        stats["errors"].append(f"ESPN API error: {e}")

    return stats


def _determine_round(event: dict) -> str:
    """Determine the tournament round from ESPN event data."""
    # Check competition type/round info
    competitions = event.get("competitions", [])
    if competitions:
        notes = competitions[0].get("notes", [])
        for note in notes:
            headline = note.get("headline", "").lower()
            for round_name in ROUND_ORDER:
                if round_name.lower() in headline:
                    return round_name

        # Try the tournament round number
        tournament = competitions[0].get("tournament", {})
        round_num = tournament.get("round", 0)
        if round_num in ESPN_ROUND_MAP:
            return ESPN_ROUND_MAP[round_num]

    # Fallback: try event name
    name = event.get("name", "").lower()
    for round_name in ROUND_ORDER:
        if round_name.lower() in name:
            return round_name

    return "Round of 64"


def _process_event(event: dict, all_teams: list[Team], db: Session, stats: dict):
    """Process a single ESPN event (game)."""
    espn_game_id = str(event.get("id", ""))
    competitions = event.get("competitions", [])
    if not competitions:
        return

    competition = competitions[0]
    competitors = competition.get("competitors", [])
    if len(competitors) != 2:
        return

    # Parse game status
    status_obj = competition.get("status", {})
    status_type = status_obj.get("type", {}).get("name", "STATUS_SCHEDULED")
    if status_type == "STATUS_FINAL":
        game_status = "final"
    elif status_type == "STATUS_IN_PROGRESS":
        game_status = "in_progress"
    else:
        game_status = "scheduled"

    # Parse teams and scores
    comp_data = []
    for comp in competitors:
        team_info = comp.get("team", {})
        espn_team_id = str(team_info.get("id", ""))
        team_name = team_info.get("displayName", team_info.get("shortDisplayName", ""))
        score = int(comp.get("score", "0") or "0")
        seed = int(comp.get("curatedRank", {}).get("current", 0) or 0)
        logo = team_info.get("logo", "")

        matched_team = _match_team(team_name, espn_team_id, all_teams)
        if matched_team and not matched_team.espn_id:
            matched_team.espn_id = espn_team_id
        if matched_team and logo and not matched_team.espn_logo_url:
            matched_team.espn_logo_url = logo

        comp_data.append({
            "team": matched_team,
            "espn_id": espn_team_id,
            "name": team_name,
            "score": score,
            "seed": seed,
        })

    # Determine round
    round_name = _determine_round(event)

    # Parse game date
    game_date = None
    date_str = event.get("date", "")
    if date_str:
        try:
            game_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    # Find or create game record
    game = db.query(Game).filter(Game.espn_game_id == espn_game_id).first()
    if game is None:
        game = Game(espn_game_id=espn_game_id, round_name=round_name)
        db.add(game)
        stats["games_created"] += 1
    else:
        stats["games_updated"] += 1

    game.round_name = round_name
    game.game_date = game_date
    game.status = game_status

    if comp_data[0]["team"]:
        game.team1_id = comp_data[0]["team"].id
    if comp_data[1]["team"]:
        game.team2_id = comp_data[1]["team"].id

    game.score1 = comp_data[0]["score"]
    game.score2 = comp_data[1]["score"]

    # Determine winner if game is final
    if game_status == "final":
        if comp_data[0]["score"] > comp_data[1]["score"] and comp_data[0]["team"]:
            game.winner_id = comp_data[0]["team"].id
            if comp_data[1]["team"]:
                comp_data[1]["team"].eliminated = True
        elif comp_data[1]["score"] > comp_data[0]["score"] and comp_data[1]["team"]:
            game.winner_id = comp_data[1]["team"].id
            if comp_data[0]["team"]:
                comp_data[0]["team"].eliminated = True
