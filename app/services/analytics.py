"""Advanced analytics using DraftKings odds from ESPN + historical fallbacks."""

from sqlalchemy.orm import Session

from app.models import Owner, Team, Game
from app.config import ROUND_PRIZES, ROUND_ORDER

# Historical fallback: probability of advancing past each round by seed.
# Used only when Vegas odds are not available for a game.
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

ROUND_PRIZE_LIST = [
    ROUND_PRIZES["Round of 64"],
    ROUND_PRIZES["Round of 32"],
    ROUND_PRIZES["Sweet 16"],
    ROUND_PRIZES["Elite 8"],
    ROUND_PRIZES["Final Four"],
    ROUND_PRIZES["Championship"],
]

ROUND_INDEX = {
    "First Four": -1,
    "Round of 64": 0,
    "Round of 32": 1,
    "Sweet 16": 2,
    "Elite 8": 3,
    "Final Four": 4,
    "Championship": 5,
}


def _get_team_win_prob(game: Game, team_id: int) -> float | None:
    """Get the Vegas-implied win probability for a team in a specific game."""
    if game.team1_id == team_id and game.team1_win_prob is not None:
        return game.team1_win_prob
    if game.team2_id == team_id and game.team2_win_prob is not None:
        return game.team2_win_prob
    return None


def _historical_round_prob(seed: int, round_idx: int) -> float:
    """Fallback: historical probability of winning in a specific round."""
    probs = SEED_ADVANCE_PROBS.get(seed, SEED_ADVANCE_PROBS[16])
    if round_idx < 0 or round_idx >= len(probs):
        return 0.0
    return probs[round_idx]


def _compute_team_ev(team: Team, all_games: list[Game], db: Session) -> dict:
    """Compute expected value for a team using Vegas odds where available.

    For each round:
    - If the game is final: use actual result (1.0 win or 0.0)
    - If the game exists with Vegas odds: use implied probability
    - If the game exists but no odds: use spread estimate or seed fallback
    - If no game exists (future round, TBD): use historical seed probability

    Returns dict with per-round probabilities and expected values.
    """
    # Find all games this team is in
    team_games = {}
    for g in all_games:
        if g.team1_id == team.id or g.team2_id == team.id:
            round_idx = ROUND_INDEX.get(g.round_name, -1)
            team_games[round_idx] = g

    round_probs = []  # P(winning in each round)
    round_sources = []  # Where the probability came from
    cumulative_prob = 1.0  # P(reaching this round)

    if team.eliminated:
        # Team is out — actual wins are known, future rounds are 0
        for i in range(6):
            game = team_games.get(i)
            if game and game.status == "final" and game.winner_id == team.id:
                round_probs.append(1.0)
                round_sources.append("result")
            else:
                round_probs.append(0.0)
                round_sources.append("eliminated")
        return {
            "round_probs": round_probs,
            "round_sources": round_sources,
        }

    for i in range(6):
        game = team_games.get(i)

        if game and game.status == "final":
            # Game completed
            if game.winner_id == team.id:
                round_probs.append(1.0)
                round_sources.append("result")
            else:
                round_probs.append(0.0)
                round_sources.append("result")
                # Team lost — zero out remaining rounds
                for j in range(i + 1, 6):
                    round_probs.append(0.0)
                    round_sources.append("eliminated")
                break
        elif game and (game.team1_win_prob is not None or game.team2_win_prob is not None):
            # Game exists with Vegas odds
            prob = _get_team_win_prob(game, team.id)
            if prob is not None:
                round_probs.append(prob)
                round_sources.append("vegas")
            else:
                # Fallback for this game
                prob = _historical_conditional_prob(team.seed, i, round_probs)
                round_probs.append(prob)
                round_sources.append("seed")
        elif game:
            # Game exists but no odds — use seed-based estimate
            prob = _historical_conditional_prob(team.seed, i, round_probs)
            round_probs.append(prob)
            round_sources.append("seed")
        else:
            # No game yet (future round) — use historical seed probability
            prob = _historical_conditional_prob(team.seed, i, round_probs)
            round_probs.append(prob)
            round_sources.append("seed")

    return {
        "round_probs": round_probs,
        "round_sources": round_sources,
    }


def _historical_conditional_prob(seed: int, round_idx: int, prior_probs: list[float]) -> float:
    """Get conditional probability of winning in round_idx given prior results."""
    probs = SEED_ADVANCE_PROBS.get(seed, SEED_ADVANCE_PROBS[16])
    if round_idx >= len(probs):
        return 0.0

    # P(win round i) = P(reach round i+1) / P(reach round i)
    # P(reach round 0) = 1.0 (they're in the tournament)
    # P(reach round i+1) = probs[i]
    if round_idx == 0:
        return probs[0]

    p_reach_this_round = probs[round_idx - 1]
    p_reach_next_round = probs[round_idx]

    if p_reach_this_round > 0:
        return p_reach_next_round / p_reach_this_round
    return 0.0


def get_analytics(db: Session) -> dict:
    """Build comprehensive analytics using Vegas odds."""
    owners = db.query(Owner).all()
    all_games = db.query(Game).all()
    completed_games = [g for g in all_games if g.status == "final"]

    # Count how many games have Vegas odds
    games_with_odds = sum(1 for g in all_games if g.team1_win_prob is not None)

    owner_analytics = []

    for owner in owners:
        teams = owner.teams
        actual_winnings = 0.0
        projected_winnings = 0.0
        max_possible = 0.0
        team_details = []

        for team in teams:
            # Actual winnings
            wins = [g for g in completed_games if g.winner_id == team.id]
            team_actual = sum(ROUND_PRIZES.get(g.round_name, 0) for g in wins)
            actual_winnings += team_actual

            # Compute EV using Vegas odds + fallback
            ev_data = _compute_team_ev(team, all_games, db)

            # Calculate expected winnings per round
            team_ev = 0.0
            round_details = []
            cumulative_p = 1.0
            for i in range(6):
                win_prob = ev_data["round_probs"][i]
                source = ev_data["round_sources"][i]
                prize = ROUND_PRIZE_LIST[i]

                if source == "result":
                    # Already happened
                    round_ev = prize if win_prob == 1.0 else 0.0
                else:
                    # Future: expected value = cumulative probability of reaching × conditional win prob × prize
                    round_ev = cumulative_p * win_prob * prize

                team_ev += round_ev
                round_details.append({
                    "round": ROUND_ORDER[i + 1],  # Skip First Four
                    "win_prob": round(win_prob, 4),
                    "source": source,
                    "ev": round(round_ev, 2),
                })

                if source == "result":
                    if win_prob == 0.0:
                        cumulative_p = 0.0
                    # If won, cumulative stays the same
                else:
                    cumulative_p *= win_prob

            # Add First Four EV for play-in teams
            first_four_ev = 0.0
            if team.is_playin:
                ff_game = next(
                    (g for g in all_games
                     if g.round_name == "First Four"
                     and (g.team1_id == team.id or g.team2_id == team.id)),
                    None,
                )
                if ff_game:
                    if ff_game.status == "final" and ff_game.winner_id == team.id:
                        first_four_ev = ROUND_PRIZES["First Four"]
                    elif ff_game.status != "final":
                        prob = _get_team_win_prob(ff_game, team.id)
                        if prob is None:
                            prob = 0.5
                        first_four_ev = prob * ROUND_PRIZES["First Four"]

            team_ev += first_four_ev
            team_projected = round(team_ev, 2)
            projected_winnings += team_projected

            # Max possible (if team wins every remaining game)
            team_max = team_actual
            if not team.eliminated:
                current_wins = len(wins)
                for i in range(current_wins, 6):
                    team_max += ROUND_PRIZE_LIST[i]
                if team.is_playin:
                    ff_game = next(
                        (g for g in all_games
                         if g.round_name == "First Four"
                         and (g.team1_id == team.id or g.team2_id == team.id)),
                        None,
                    )
                    if ff_game and ff_game.status != "final":
                        team_max += ROUND_PRIZES["First Four"]
            max_possible += team_max

            # Vegas line display
            current_game = next(
                (g for g in all_games
                 if g.status == "scheduled"
                 and (g.team1_id == team.id or g.team2_id == team.id)),
                None,
            )
            vegas_line = None
            if current_game and current_game.spread is not None:
                if current_game.team1_id == team.id:
                    vegas_line = current_game.spread
                else:
                    vegas_line = -current_game.spread

            team_details.append({
                "team": team,
                "actual": round(team_actual, 2),
                "projected": team_projected,
                "max_possible": round(team_max, 2),
                "wins": len(wins),
                "round_details": round_details,
                "vegas_line": vegas_line,
                "first_four_ev": round(first_four_ev, 2),
            })

        team_details.sort(key=lambda t: (t["team"].eliminated, -t["projected"]))

        owner_analytics.append({
            "owner": owner,
            "actual_winnings": round(actual_winnings, 2),
            "projected_winnings": round(projected_winnings, 2),
            "max_possible": round(max_possible, 2),
            "active_teams": sum(1 for t in teams if not t.eliminated),
            "total_teams": len(teams),
            "teams": team_details,
        })

    owner_analytics.sort(key=lambda o: -o["projected_winnings"])

    # Over/underperformers: teams with biggest gap between actual and EV
    all_team_details = []
    for oa in owner_analytics:
        for td in oa["teams"]:
            td["owner_name"] = oa["owner"].name
            all_team_details.append(td)

    total_actual = sum(o["actual_winnings"] for o in owner_analytics)

    return {
        "owners": owner_analytics,
        "total_pot": round(total_actual, 2),
        "games_played": len(completed_games),
        "games_with_odds": games_with_odds,
        "total_games": len(all_games),
    }
