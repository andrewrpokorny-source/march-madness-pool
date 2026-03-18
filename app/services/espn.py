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
FUTURES_URL = (
    "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
    "/seasons/2026/futures"
)

# ESPN uses group 100 for NCAA Tournament
TOURNAMENT_PARAMS = {"groups": "100", "limit": "100"}

# Midwest must come before West to avoid substring match
REGIONS = ["South", "East", "Midwest", "West"]

# ESPN headline round phrases -> our round names
# Checked in order, so more specific matches come first
ESPN_HEADLINE_ROUND_MAP = [
    ("first four", "First Four"),
    ("1st round", "Round of 64"),
    ("2nd round", "Round of 32"),
    ("sweet 16", "Sweet 16"),
    ("elite 8", "Elite 8"),
    ("elite eight", "Elite 8"),
    ("final four", "Final Four"),
    ("national championship", "Championship"),
    ("championship game", "Championship"),
    # These must come AFTER more specific matches
    ("semifinal", "Final Four"),
]

# Direct mapping from ESPN team display names to our draft names.
# This avoids fragile normalization logic.
ESPN_TEAM_MAP = {
    "Michigan Wolverines": "Michigan",
    "Kansas Jayhawks": "Kansas",
    "Tennessee Volunteers": "Tennessee",
    "Utah State Aggies": "Utah State",
    "Santa Clara Broncos": "Santa Clara",
    "South Florida Bulls": "South Florida",
    "McNeese Cowboys": "McNeese",
    "Siena Saints": "Siena",
    "Arizona Wildcats": "Arizona",
    "Michigan State Spartans": "Michigan State",
    "Alabama Crimson Tide": "Alabama",
    "Villanova Wildcats": "Villanova",
    "VCU Rams": "VCU",
    "Troy Trojans": "Troy",
    "Queens University Royals": "Queens",
    "Furman Paladins": "Furman",
    "Duke Blue Devils": "Duke",
    "Gonzaga Bulldogs": "Gonzaga",
    "Texas Tech Red Raiders": "Texas Tech",
    "Saint Mary's Gaels": "St. Mary's",
    "Northern Iowa Panthers": "N Iowa",
    "Hawai'i Rainbow Warriors": "Hawaii",
    "Prairie View A&M Panthers": "Prairie View",
    "Lehigh Mountain Hawks": "Lehigh",
    "Florida Gators": "Florida",
    "Illinois Fighting Illini": "Illinois",
    "Louisville Cardinals": "Louisville",
    "Iowa Hawkeyes": "Iowa",
    "Missouri Tigers": "Missouri",
    "Texas Longhorns": "Texas",
    "NC State Wolfpack": "NC State",
    "Hofstra Pride": "Hofstra",
    "Howard Bison": "Howard",
    "UMBC Retrievers": "UMBC",
    "Houston Cougars": "Houston",
    "Nebraska Cornhuskers": "Nebraska",
    "Vanderbilt Commodores": "Vanderbilt",
    "UCLA Bruins": "UCLA",
    "Miami Hurricanes": "Miami",  # Michael's 7-seed
    "SMU Mustangs": "SMU",       # Brittany's play-in
    "High Point Panthers": "High Point",
    "Pennsylvania Quakers": "Penn",
    "Idaho Vandals": "Idaho",
    "Iowa State Cyclones": "Iowa State",
    "St. John's Red Storm": "St. John's",
    "Wisconsin Badgers": "Wisconsin",
    "Ohio State Buckeyes": "OSU",
    "Georgia Bulldogs": "Georgia",
    "Texas A&M Aggies": "Texas A&M",
    "California Baptist Lancers": "Cal Baptist",
    "Tennessee State Tigers": "Tennessee State",
    "UConn Huskies": "UConn",
    "Purdue Boilermakers": "Purdue",
    "BYU Cougars": "BYU",
    "Miami (OH) RedHawks": "Miami OH",  # Brittany's play-in
    "Clemson Tigers": "Clemson",
    "Saint Louis Billikens": "St. Louis",
    "Wright State Raiders": "Wright State",
    "North Dakota State Bison": "N Dakota State",
    "Virginia Cavaliers": "Virginia",
    "Arkansas Razorbacks": "Arkansas",
    "North Carolina Tar Heels": "UNC",
    "TCU Horned Frogs": "TCU",
    "UCF Knights": "UCF",
    "Akron Zips": "Akron",
    "Kennesaw State Owls": "Kennesaw State",
    "Long Island University Sharks": "LIU",
    "Kentucky Wildcats": "Kentucky",
}


def _match_team(espn_name: str, espn_id: str, teams: list[Team]) -> Team | None:
    """Match an ESPN team to one of our drafted teams."""
    # First try by espn_id if already set
    for team in teams:
        if team.espn_id == espn_id:
            return team

    # Try direct name map
    draft_name = ESPN_TEAM_MAP.get(espn_name)
    if draft_name:
        for team in teams:
            if team.name == draft_name:
                return team
            # Also check play-in label
            if team.playin_label and team.playin_label == draft_name:
                return team

    # Fallback: check if ESPN name starts with our team name
    espn_lower = espn_name.lower()
    for team in teams:
        team_lower = team.name.lower()
        if espn_lower.startswith(team_lower) or team_lower.startswith(espn_lower):
            return team

    logger.warning(f"Could not match ESPN team: {espn_name} (id={espn_id})")
    return None


def _get_tournament_dates() -> list[str]:
    """Generate date strings covering the full tournament window."""
    from datetime import date, timedelta

    today = date.today()
    start = today - timedelta(days=5)
    dates = []
    for i in range(30):
        d = start + timedelta(days=i)
        dates.append(d.strftime("%Y%m%d"))
    return dates


async def fetch_tournament_scores(db: Session) -> dict:
    """Fetch tournament scores from ESPN across all tournament dates."""
    all_teams = db.query(Team).all()
    stats = {"games_updated": 0, "games_created": 0, "errors": []}

    tournament_dates = _get_tournament_dates()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for date_str in tournament_dates:
                try:
                    params = {**TOURNAMENT_PARAMS, "dates": date_str}
                    resp = await client.get(SCOREBOARD_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()

                    events = data.get("events", [])
                    if events:
                        logger.info(f"ESPN {date_str}: {len(events)} events")

                    for event in events:
                        try:
                            _process_event(event, all_teams, db, stats)
                        except Exception as e:
                            logger.error(f"Error processing event: {e}")
                            stats["errors"].append(str(e))

                except httpx.HTTPError as e:
                    logger.error(f"ESPN API error for {date_str}: {e}")
                    stats["errors"].append(f"ESPN {date_str}: {e}")

        # Fetch championship futures odds
        try:
            await _fetch_futures(client, all_teams, stats)
        except Exception as e:
            logger.error(f"Futures fetch error: {e}")
            stats["errors"].append(f"Futures: {e}")

        db.commit()
    except Exception as e:
        logger.error(f"ESPN sync error: {e}")
        stats["errors"].append(str(e))

    return stats


async def _fetch_futures(client: httpx.AsyncClient, all_teams: list[Team], stats: dict):
    """Fetch championship futures odds from ESPN and store on Team records."""
    import re

    # First, find the championship winner futures ID
    resp = await client.get(FUTURES_URL, params={"lang": "en", "region": "us"})
    resp.raise_for_status()
    index = resp.json()

    champ_url = None
    for item in index.get("items", []):
        ref = item.get("$ref", "")
        # The championship winner future — fetch each to check the name,
        # or use the last one (typically the winner market has the highest ID)
        champ_url = ref  # We'll check them all below

    # Try all futures to find the "Winner" market
    for item in index.get("items", []):
        ref = item.get("$ref", "").replace("http://", "https://")
        if not ref:
            continue
        try:
            resp = await client.get(ref)
            resp.raise_for_status()
            data = resp.json()
            name = data.get("name", "")
            if "Winner" not in name and "winner" not in name.lower():
                continue

            # Found championship winner futures
            futures_list = data.get("futures", [])
            if not futures_list:
                continue

            books = futures_list[0].get("books", [])
            espn_id_map = {t.espn_id: t for t in all_teams if t.espn_id}
            matched = 0

            for book in books:
                team_ref = book.get("team", {}).get("$ref", "")
                # Extract team ID from URL like .../teams/130?...
                m = re.search(r"/teams/(\d+)", team_ref)
                if not m:
                    continue
                espn_id = m.group(1)
                odds_str = book.get("value", "")
                try:
                    odds_val = int(odds_str.replace("+", ""))
                except (ValueError, AttributeError):
                    continue

                team = espn_id_map.get(espn_id)
                if team:
                    team.championship_odds = odds_val
                    matched += 1

            logger.info(f"Championship futures: matched {matched}/{len(books)} teams")
            stats["futures_matched"] = matched
            return

        except httpx.HTTPError:
            continue

    logger.warning("Could not find championship winner futures market")


def _determine_round(event: dict) -> str:
    """Determine the tournament round from ESPN event data."""
    competitions = event.get("competitions", [])
    if competitions:
        notes = competitions[0].get("notes", [])
        for note in notes:
            headline = note.get("headline", "").lower()
            # Check specific round phrases (order matters)
            for espn_phrase, our_round in ESPN_HEADLINE_ROUND_MAP:
                if espn_phrase in headline:
                    return our_round

    # Fallback: try event name
    name = event.get("name", "").lower()
    for espn_phrase, our_round in ESPN_HEADLINE_ROUND_MAP:
        if espn_phrase in name:
            return our_round

    return "Round of 64"


def _parse_odds(competition: dict) -> tuple[float | None, float | None, float | None]:
    """Extract win probabilities and spread from ESPN odds data.

    Converts American moneyline odds to implied probability, then
    removes the vig by normalizing to sum to 1.0.
    """
    odds_list = competition.get("odds", [])
    if not odds_list:
        return None, None, None

    odds = odds_list[0]
    spread = odds.get("spread")

    moneyline = odds.get("moneyline", {})
    home_ml = moneyline.get("home", {}).get("close", {}).get("odds")
    away_ml = moneyline.get("away", {}).get("close", {}).get("odds")

    if not home_ml or not away_ml:
        # Fall back to spread-based estimate if no moneyline
        if spread is not None:
            # Rough conversion: each point of spread ≈ 3% win probability shift
            home_prob = 0.50 + (abs(float(spread)) * 0.03) * (1 if float(spread) < 0 else -1)
            home_prob = max(0.02, min(0.98, home_prob))
            return home_prob, 1 - home_prob, float(spread)
        return None, None, None

    home_ml = float(home_ml)
    away_ml = float(away_ml)

    # Convert American odds to implied probability
    def _american_to_prob(odds: float) -> float:
        if odds < 0:
            return abs(odds) / (abs(odds) + 100)
        else:
            return 100 / (odds + 100)

    home_imp = _american_to_prob(home_ml)
    away_imp = _american_to_prob(away_ml)

    # Remove vig by normalizing
    total = home_imp + away_imp
    if total > 0:
        home_prob = home_imp / total
        away_prob = away_imp / total
    else:
        home_prob, away_prob = 0.5, 0.5

    return home_prob, away_prob, float(spread) if spread else None


def _extract_region(event: dict) -> str | None:
    """Extract region name from ESPN event data."""
    competitions = event.get("competitions", [])
    if competitions:
        notes = competitions[0].get("notes", [])
        for note in notes:
            headline = note.get("headline", "")
            for region in REGIONS:
                if region.lower() in headline.lower():
                    return region
    name = event.get("name", "")
    for region in REGIONS:
        if region.lower() in name.lower():
            return region
    return None


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

    # Determine round and region
    round_name = _determine_round(event)
    region = _extract_region(event)

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
    game.region = region
    game.game_date = game_date
    game.status = game_status

    if comp_data[0]["team"]:
        game.team1_id = comp_data[0]["team"].id
    if comp_data[1]["team"]:
        game.team2_id = comp_data[1]["team"].id

    game.score1 = comp_data[0]["score"]
    game.score2 = comp_data[1]["score"]

    # Parse odds — ESPN competitors[0] is away, competitors[1] is home
    # but our team1/team2 follows the same competitor order
    home_prob, away_prob, spread_val = _parse_odds(competition)
    if home_prob is not None:
        # competitors[0] = away, competitors[1] = home in ESPN data
        # Check homeAway field to be safe
        comp0_ha = competitors[0].get("homeAway", "away")
        if comp0_ha == "home":
            game.team1_win_prob = round(home_prob, 4)
            game.team2_win_prob = round(away_prob, 4)
            # ESPN spread is from home perspective (negative = home favored)
            # team1 = home here, so keep sign as-is for team1 perspective
            game.spread = round(spread_val, 1) if spread_val else None
        else:
            game.team1_win_prob = round(away_prob, 4)
            game.team2_win_prob = round(home_prob, 4)
            # team1 = away here, so negate to get team1 perspective
            game.spread = round(-spread_val, 1) if spread_val else None

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
