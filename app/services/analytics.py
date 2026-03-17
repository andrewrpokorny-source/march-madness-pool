"""Advanced analytics using historical seed win probabilities."""

from sqlalchemy.orm import Session

from app.models import Owner, Team, Game
from app.config import ROUND_PRIZES, ROUND_ORDER

# Historical probability of a seed advancing past each round.
# Source: aggregated NCAA tournament data 1985-2024.
# Index 0 = P(win R64), 1 = P(win R64 & R32), ..., 5 = P(win all 6 = champion)
# For First Four teams, we halve the base seed probability.
SEED_ADVANCE_PROBS = {
    1:  [0.993, 0.850, 0.600, 0.420, 0.270, 0.150],
    2:  [0.943, 0.720, 0.440, 0.275, 0.155, 0.080],
    3:  [0.850, 0.580, 0.300, 0.170, 0.080, 0.040],
    4:  [0.793, 0.500, 0.250, 0.120, 0.050, 0.025],
    5:  [0.643, 0.350, 0.150, 0.070, 0.030, 0.012],
    6:  [0.629, 0.330, 0.140, 0.060, 0.025, 0.010],
    7:  [0.607, 0.300, 0.120, 0.050, 0.020, 0.008],
    8:  [0.500, 0.240, 0.090, 0.038, 0.015, 0.006],
    9:  [0.500, 0.220, 0.080, 0.032, 0.012, 0.004],
    10: [0.393, 0.180, 0.063, 0.025, 0.008, 0.003],
    11: [0.371, 0.160, 0.056, 0.022, 0.008, 0.003],
    12: [0.357, 0.150, 0.048, 0.015, 0.005, 0.002],
    13: [0.207, 0.065, 0.018, 0.005, 0.001, 0.0005],
    14: [0.150, 0.045, 0.010, 0.003, 0.001, 0.0003],
    15: [0.057, 0.013, 0.004, 0.001, 0.0002, 0.0001],
    16: [0.007, 0.002, 0.0005, 0.0001, 0.00003, 0.00001],
}

# Prize per round (indexed same as SEED_ADVANCE_PROBS)
ROUND_PRIZE_LIST = [
    ROUND_PRIZES["Round of 64"],    # $1
    ROUND_PRIZES["Round of 32"],    # $2
    ROUND_PRIZES["Sweet 16"],       # $5
    ROUND_PRIZES["Elite 8"],        # $10
    ROUND_PRIZES["Final Four"],     # $20
    ROUND_PRIZES["Championship"],   # $50
]

# Map round name to index for determining how far a team has advanced
ROUND_INDEX = {
    "First Four": -1,
    "Round of 64": 0,
    "Round of 32": 1,
    "Sweet 16": 2,
    "Elite 8": 3,
    "Final Four": 4,
    "Championship": 5,
}


def _expected_winnings_for_seed(seed: int, is_playin: bool = False) -> float:
    """Calculate pre-tournament expected winnings for a team by seed."""
    probs = SEED_ADVANCE_PROBS.get(seed, SEED_ADVANCE_PROBS[16])
    if is_playin:
        # Play-in teams have ~50% chance of even reaching R64
        probs = [p * 0.5 for p in probs]
        # Add First Four prize: 50% chance of winning play-in game
        first_four_ev = 0.5 * ROUND_PRIZES["First Four"]
    else:
        first_four_ev = 0

    ev = first_four_ev
    for i, prob in enumerate(probs):
        ev += prob * ROUND_PRIZE_LIST[i]
    return round(ev, 2)


def _current_round_index(team: Team, db: Session) -> int:
    """Determine how far a team has advanced (highest round won)."""
    if team.eliminated:
        # Find the last round they won
        last_win = (
            db.query(Game)
            .filter(Game.winner_id == team.id, Game.status == "final")
            .all()
        )
        if not last_win:
            return -1
        max_idx = max(ROUND_INDEX.get(g.round_name, -1) for g in last_win)
        return max_idx
    else:
        wins = (
            db.query(Game)
            .filter(Game.winner_id == team.id, Game.status == "final")
            .all()
        )
        if not wins:
            return -1
        return max(ROUND_INDEX.get(g.round_name, -1) for g in wins)


def _remaining_ev(team: Team, current_round_idx: int) -> float:
    """Expected value from remaining rounds for a team still alive."""
    if team.eliminated:
        return 0.0

    seed = team.seed
    probs = SEED_ADVANCE_PROBS.get(seed, SEED_ADVANCE_PROBS[16])

    ev = 0.0
    for i in range(current_round_idx + 1, len(probs)):
        # Conditional probability of winning round i given reached it
        if current_round_idx < 0:
            # Haven't won any games yet
            cond_prob = probs[i]
        else:
            # Conditional: P(advance past round i | advanced past current)
            p_reached = probs[current_round_idx] if current_round_idx >= 0 else 1.0
            if p_reached > 0:
                cond_prob = probs[i] / p_reached
            else:
                cond_prob = 0
        ev += cond_prob * ROUND_PRIZE_LIST[i]

    return round(ev, 2)


def _max_remaining(team: Team, current_round_idx: int) -> float:
    """Maximum possible winnings if a team wins every remaining game."""
    if team.eliminated:
        return 0.0

    total = 0.0
    for i in range(current_round_idx + 1, len(ROUND_PRIZE_LIST)):
        total += ROUND_PRIZE_LIST[i]
    return total


def get_analytics(db: Session) -> dict:
    """Build comprehensive analytics data."""
    owners = db.query(Owner).all()
    all_games = db.query(Game).filter(Game.status == "final").all()

    owner_analytics = []

    for owner in owners:
        teams = owner.teams
        actual_winnings = 0.0
        pre_tournament_ev = 0.0
        projected_winnings = 0.0
        max_possible = 0.0
        team_details = []

        for team in teams:
            # Pre-tournament EV
            team_pre_ev = _expected_winnings_for_seed(team.seed, team.is_playin)
            pre_tournament_ev += team_pre_ev

            # Actual winnings so far
            wins = [g for g in all_games if g.winner_id == team.id]
            team_actual = sum(ROUND_PRIZES.get(g.round_name, 0) for g in wins)
            actual_winnings += team_actual

            # Current advancement
            current_idx = _current_round_index(team, db)

            # Remaining EV
            team_remaining_ev = _remaining_ev(team, current_idx)
            projected_winnings += team_actual + team_remaining_ev

            # Max upside
            team_max = team_actual + _max_remaining(team, current_idx)
            max_possible += team_max

            team_details.append({
                "team": team,
                "pre_ev": team_pre_ev,
                "actual": team_actual,
                "remaining_ev": team_remaining_ev,
                "projected": round(team_actual + team_remaining_ev, 2),
                "max_possible": team_max,
                "wins": len(wins),
                "current_round": current_idx,
                "performance": round(team_actual - team_pre_ev, 2),
            })

        # Sort team details: alive first, then by projected desc
        team_details.sort(key=lambda t: (t["team"].eliminated, -t["projected"]))

        owner_analytics.append({
            "owner": owner,
            "pre_tournament_ev": round(pre_tournament_ev, 2),
            "actual_winnings": round(actual_winnings, 2),
            "projected_winnings": round(projected_winnings, 2),
            "max_possible": round(max_possible, 2),
            "active_teams": sum(1 for t in teams if not t.eliminated),
            "total_teams": len(teams),
            "teams": team_details,
            "performance_vs_expected": round(actual_winnings - pre_tournament_ev, 2),
        })

    # Sort by projected winnings
    owner_analytics.sort(key=lambda o: -o["projected_winnings"])

    # Find overperformers and underperformers across all teams
    all_team_details = []
    for oa in owner_analytics:
        for td in oa["teams"]:
            td["owner_name"] = oa["owner"].name
            all_team_details.append(td)

    overperformers = sorted(all_team_details, key=lambda t: -t["performance"])[:5]
    underperformers = sorted(all_team_details, key=lambda t: t["performance"])[:5]

    # Total pot
    total_actual = sum(o["actual_winnings"] for o in owner_analytics)

    return {
        "owners": owner_analytics,
        "overperformers": overperformers,
        "underperformers": underperformers,
        "total_pot": round(total_actual, 2),
        "games_played": len(all_games),
    }
