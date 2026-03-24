"""Advanced analytics using DraftKings odds from ESPN + historical fallbacks."""

import logging
import math
import random
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import Owner, Team, Game
from app.config import ROUND_PRIZES, ROUND_ORDER

logger = logging.getLogger(__name__)

# Historical fallback: probability of advancing past each round by seed.
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
    if game.team1_id == team_id and game.team1_win_prob is not None:
        return game.team1_win_prob
    if game.team2_id == team_id and game.team2_win_prob is not None:
        return game.team2_win_prob
    return None


def _american_odds_to_prob(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def _historical_round_prob(seed: int, round_idx: int) -> float:
    probs = SEED_ADVANCE_PROBS.get(seed, SEED_ADVANCE_PROBS[16])
    if round_idx < 0 or round_idx >= len(probs):
        return 0.0
    return probs[round_idx]


def _calibrate_future_probs(
    seed: int,
    first_future_round: int,
    known_product: float,
    champ_prob: float,
) -> list[float]:
    """Derive per-round conditional win probs for future rounds using futures odds."""
    num_future = 6 - first_future_round
    if num_future <= 0:
        return []
    if known_product <= 0:
        return [0.0] * num_future

    target_product = champ_prob / known_product
    target_product = max(0.0, min(1.0, target_product))
    if target_product <= 0:
        return [0.0] * num_future

    seed_probs = [_historical_conditional_prob(seed, i, []) for i in range(first_future_round, 6)]
    seed_product = 1.0
    for p in seed_probs:
        seed_product *= max(p, 1e-10)

    if seed_product <= 0:
        return [target_product ** (1.0 / num_future)] * num_future

    log_target = math.log(max(target_product, 1e-15))
    log_seed = math.log(max(seed_product, 1e-15))
    if abs(log_seed) < 1e-10:
        return [target_product ** (1.0 / num_future)] * num_future

    scale = log_target / log_seed
    return [round(max(0.001, min(0.99, p ** scale)), 6) for p in seed_probs]


def _compute_team_ev(team: Team, all_games: list[Game], db: Session) -> dict:
    """Compute expected value for a team using Vegas odds where available."""
    team_games = {}
    for g in all_games:
        if g.team1_id == team.id or g.team2_id == team.id:
            round_idx = ROUND_INDEX.get(g.round_name, -1)
            team_games[round_idx] = g

    round_probs = []
    round_sources = []

    if team.eliminated:
        for i in range(6):
            game = team_games.get(i)
            if game and game.status == "final" and game.winner_id == team.id:
                round_probs.append(1.0)
                round_sources.append("result")
            else:
                round_probs.append(0.0)
                round_sources.append("eliminated")
        return {"round_probs": round_probs, "round_sources": round_sources}

    first_future_round = None
    known_product = 1.0

    for i in range(6):
        game = team_games.get(i)
        if game and game.status == "final":
            if game.winner_id == team.id:
                round_probs.append(1.0)
                round_sources.append("result")
            else:
                round_probs.append(0.0)
                round_sources.append("result")
                for j in range(i + 1, 6):
                    round_probs.append(0.0)
                    round_sources.append("eliminated")
                return {"round_probs": round_probs, "round_sources": round_sources}
        elif game and (game.team1_win_prob is not None or game.team2_win_prob is not None):
            prob = _get_team_win_prob(game, team.id)
            if prob is not None:
                round_probs.append(prob)
                round_sources.append("vegas")
                known_product *= prob
            else:
                first_future_round = i
                break
        else:
            first_future_round = i
            break

    if first_future_round is None:
        return {"round_probs": round_probs, "round_sources": round_sources}

    champ_prob = None
    if team.championship_odds is not None:
        champ_prob = _american_odds_to_prob(team.championship_odds)

    if champ_prob is not None and champ_prob > 0:
        calibrated = _calibrate_future_probs(team.seed, first_future_round, known_product, champ_prob)
        for cp in calibrated:
            round_probs.append(cp)
            round_sources.append("futures")
    else:
        for i in range(first_future_round, 6):
            prob = _historical_conditional_prob(team.seed, i, round_probs)
            round_probs.append(prob)
            round_sources.append("seed")

    return {"round_probs": round_probs, "round_sources": round_sources}


def _historical_conditional_prob(seed: int, round_idx: int, prior_probs: list[float]) -> float:
    probs = SEED_ADVANCE_PROBS.get(seed, SEED_ADVANCE_PROBS[16])
    if round_idx >= len(probs):
        return 0.0
    if round_idx == 0:
        return probs[0]
    p_reach_this = probs[round_idx - 1]
    p_reach_next = probs[round_idx]
    return p_reach_next / p_reach_this if p_reach_this > 0 else 0.0


def _count_effective_teams(teams: list[Team], alive_only: bool = False) -> int:
    """Count teams treating each play-in pair as one slot."""
    seen = set()
    count = 0
    for t in teams:
        if alive_only and t.eliminated:
            continue
        if t.playin_label:
            if t.playin_label not in seen:
                seen.add(t.playin_label)
                count += 1
        else:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Monte Carlo simulation — estimate pool win probability for each owner
# ---------------------------------------------------------------------------

def _simulate_pool(owner_analytics: list[dict], all_games: list[Game],
                   team_ev_cache: dict, n_sims: int = 10000) -> dict:
    """Run Monte Carlo simulation to estimate each owner's probability of winning the pool.

    For each simulation:
    - Uses actual winnings as the base
    - For each alive team, simulates each remaining round using that team's
      per-round conditional win probabilities (from _compute_team_ev)
    - Awards the round prize each time a team survives
    - Ranks owners by total simulated winnings

    Returns dict mapping owner_name -> {win_pct, top3_pct, avg_finish}
    """
    # Build per-team simulation data: owner_name, round_probs, first_pending_round
    team_sim_data = []  # list of (owner_name, round_probs_for_remaining_rounds)

    for oa in owner_analytics:
        owner_name = oa["owner"].name
        seen_playin = set()
        for td in oa["teams"]:
            team = td["team"]
            if team.eliminated:
                continue
            # Skip duplicate play-in teams (only simulate one per pair)
            if team.playin_label:
                if team.playin_label in seen_playin:
                    continue
                seen_playin.add(team.playin_label)

            ev_data = team_ev_cache.get(team.id)
            if not ev_data:
                continue

            # Find which rounds are still pending (not "result")
            remaining = []  # list of (round_index, conditional_win_prob, prize)
            for i in range(6):
                source = ev_data["round_sources"][i]
                if source in ("result",):
                    continue
                if source == "eliminated":
                    break
                prob = ev_data["round_probs"][i]
                remaining.append((i, prob, ROUND_PRIZE_LIST[i]))

            if remaining:
                team_sim_data.append((owner_name, remaining))

    # Pre-compute actual winnings base
    owner_actual = {oa["owner"].name: oa["actual_winnings"] for oa in owner_analytics}
    owner_names = list(owner_actual.keys())
    win_counts = {n: 0 for n in owner_names}
    top3_counts = {n: 0 for n in owner_names}
    finish_sum = {n: 0 for n in owner_names}

    for _ in range(n_sims):
        sim_totals = dict(owner_actual)

        for owner_name, remaining_rounds in team_sim_data:
            # Simulate this team through its remaining rounds
            alive = True
            for round_idx, win_prob, prize in remaining_rounds:
                if not alive:
                    break
                if random.random() < win_prob:
                    sim_totals[owner_name] += prize
                else:
                    alive = False

        # Rank owners
        ranked = sorted(owner_names, key=lambda n: sim_totals[n], reverse=True)
        for rank, name in enumerate(ranked):
            if rank == 0:
                win_counts[name] += 1
            if rank < 3:
                top3_counts[name] += 1
            finish_sum[name] += rank + 1

    results = {}
    for name in owner_names:
        results[name] = {
            "win_pct": round(win_counts[name] / n_sims * 100, 1),
            "top3_pct": round(top3_counts[name] / n_sims * 100, 1),
            "avg_finish": round(finish_sum[name] / n_sims, 1),
        }
    return results


# ---------------------------------------------------------------------------
# Rooting guide — which upcoming games matter most for each owner
# ---------------------------------------------------------------------------

def _build_rooting_guide(all_games: list[Game], owner_analytics: list[dict]) -> list[dict]:
    """For each upcoming game, compute which owner benefits and by how much.

    Returns list of dicts with game info + per-owner EV swing.
    """
    # Map team_id -> owner_name
    team_owner = {}
    for oa in owner_analytics:
        for td in oa["teams"]:
            team_owner[td["team"].id] = oa["owner"].name

    guide = []
    for g in all_games:
        if g.status == "final" or not g.team1_id or not g.team2_id:
            continue
        if g.round_name == "First Four":
            continue

        prize = ROUND_PRIZES.get(g.round_name, 0)
        owner1 = team_owner.get(g.team1_id)
        owner2 = team_owner.get(g.team2_id)

        p1 = g.team1_win_prob or 0.5
        p2 = g.team2_win_prob or (1 - p1)

        entry = {
            "game": g,
            "team1_name": g.team1.name if g.team1 else "TBD",
            "team2_name": g.team2.name if g.team2 else "TBD",
            "team1_seed": g.team1.seed if g.team1 else 0,
            "team2_seed": g.team2.seed if g.team2 else 0,
            "owner1": owner1,
            "owner2": owner2,
            "round_name": g.round_name,
            "prize": prize,
            "team1_prob": round(p1, 3),
            "team2_prob": round(p2, 3),
            "head_to_head": owner1 != owner2 and owner1 and owner2,
            "same_owner": owner1 == owner2 and owner1 is not None,
            "status": g.status,
        }

        # Swing: how much does each outcome change EV?
        # If team1 wins: owner1 gets $prize, owner2 gets $0
        # If team2 wins: owner2 gets $prize, owner1 gets $0
        # EV swing for owner1 = prize * (1 - p1) if team1 wins (they gain the unexpected part)
        if owner1 and owner2 and owner1 != owner2:
            entry["swing"] = prize  # Full prize at stake between two owners
        elif owner1 == owner2:
            entry["swing"] = 0  # Guaranteed money either way
        else:
            entry["swing"] = prize

        guide.append(entry)

    # Sort: live games first, then by prize (bigger stakes first), then by date
    guide.sort(key=lambda e: (
        0 if e["status"] == "in_progress" else 1,
        -e["prize"],
        e["game"].game_date or "",
    ))

    return guide


# ---------------------------------------------------------------------------
# Round-by-round expected earnings breakdown
# ---------------------------------------------------------------------------

def _round_earnings_breakdown(owner_analytics: list[dict]) -> list[dict]:
    """For each owner, compute expected earnings per round."""
    round_names = ["R64", "R32", "S16", "E8", "F4", "CH"]
    breakdown = []
    for oa in owner_analytics:
        per_round = [0.0] * 6
        for td in oa["teams"]:
            for i, rd in enumerate(td["round_details"]):
                per_round[i] += rd["ev"]
        breakdown.append({
            "owner": oa["owner"].name,
            "rounds": [round(v, 2) for v in per_round],
            "total": round(sum(per_round), 2),
        })
    return breakdown


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_analytics(db: Session) -> dict:
    """Build comprehensive analytics using Vegas odds."""
    owners = db.query(Owner).all()
    all_games = db.query(Game).all()
    completed_games = [g for g in all_games if g.status == "final"]

    games_with_odds = sum(1 for g in all_games if g.team1_win_prob is not None)
    all_team_objs = db.query(Team).all()
    teams_with_futures = sum(1 for t in all_team_objs if t.championship_odds is not None)

    owner_analytics = []
    team_ev_cache = {}

    for owner in owners:
        teams = owner.teams
        actual_winnings = 0.0
        projected_winnings = 0.0
        max_possible = 0.0
        team_details = []

        for team in teams:
            wins = [g for g in completed_games if g.winner_id == team.id]
            team_actual = sum(ROUND_PRIZES.get(g.round_name, 0) for g in wins)
            actual_winnings += team_actual

            ev_data = _compute_team_ev(team, all_games, db)
            team_ev_cache[team.id] = ev_data

            team_ev = 0.0
            round_details = []
            cumulative_p = 1.0
            for i in range(6):
                win_prob = ev_data["round_probs"][i]
                source = ev_data["round_sources"][i]
                prize = ROUND_PRIZE_LIST[i]

                if source == "result":
                    round_ev = prize if win_prob == 1.0 else 0.0
                else:
                    round_ev = cumulative_p * win_prob * prize

                team_ev += round_ev
                round_details.append({
                    "round": ROUND_ORDER[i + 1],
                    "win_prob": round(win_prob, 4),
                    "source": source,
                    "ev": round(round_ev, 2),
                })

                if source == "result":
                    if win_prob == 0.0:
                        cumulative_p = 0.0
                else:
                    cumulative_p *= win_prob

            # First Four EV
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
                        prob = _get_team_win_prob(ff_game, team.id) or 0.5
                        first_four_ev = prob * ROUND_PRIZES["First Four"]

            team_ev += first_four_ev
            team_projected = round(team_ev, 2)
            projected_winnings += team_projected

            # Max possible
            team_max = team_actual
            if not team.eliminated:
                for i in range(len(wins), 6):
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

            # Championship probability for display
            champ_p = None
            if team.championship_odds is not None and not team.eliminated:
                champ_p = round(_american_odds_to_prob(team.championship_odds) * 100, 2)

            # Vegas line display
            current_game = next(
                (g for g in all_games
                 if g.status != "final"
                 and (g.team1_id == team.id or g.team2_id == team.id)
                 and g.round_name != "First Four"),
                None,
            )
            vegas_line = None
            if current_game and current_game.spread is not None:
                vegas_line = current_game.spread if current_game.team1_id == team.id else -current_game.spread

            team_details.append({
                "team": team,
                "actual": round(team_actual, 2),
                "projected": team_projected,
                "max_possible": round(team_max, 2),
                "wins": len(wins),
                "round_details": round_details,
                "vegas_line": vegas_line,
                "first_four_ev": round(first_four_ev, 2),
                "champ_pct": champ_p,
            })

        team_details.sort(key=lambda t: (t["team"].eliminated, -t["projected"]))

        # Deduplicate max_possible for play-in pairs
        seen_playin = {}
        adjusted_max = 0.0
        for td in team_details:
            t = td["team"]
            if t.playin_label:
                prev = seen_playin.get(t.playin_label)
                if prev is None:
                    seen_playin[t.playin_label] = td["max_possible"]
                    adjusted_max += td["max_possible"]
                elif td["max_possible"] > prev:
                    adjusted_max += td["max_possible"] - prev
                    seen_playin[t.playin_label] = td["max_possible"]
            else:
                adjusted_max += td["max_possible"]

        # Best remaining team (highest projected)
        best_team = None
        alive_teams = [td for td in team_details if not td["team"].eliminated]
        if alive_teams:
            best_team = max(alive_teams, key=lambda t: t["projected"])

        owner_analytics.append({
            "owner": owner,
            "actual_winnings": round(actual_winnings, 2),
            "projected_winnings": round(projected_winnings, 2),
            "max_possible": round(adjusted_max, 2),
            "active_teams": _count_effective_teams(teams, alive_only=True),
            "total_teams": _count_effective_teams(teams, alive_only=False),
            "teams": team_details,
            "best_team": best_team,
        })

    owner_analytics.sort(key=lambda o: -o["projected_winnings"])

    # Monte Carlo pool win simulation
    sim_results = _simulate_pool(owner_analytics, all_games, team_ev_cache)
    for oa in owner_analytics:
        oa["sim"] = sim_results.get(oa["owner"].name, {"win_pct": 0, "top3_pct": 0, "avg_finish": 4})

    # Rooting guide
    rooting_guide = _build_rooting_guide(all_games, owner_analytics)

    # Round-by-round breakdown
    round_breakdown = _round_earnings_breakdown(owner_analytics)

    # MVP team (highest projected across all owners)
    all_alive = []
    for oa in owner_analytics:
        for td in oa["teams"]:
            if not td["team"].eliminated:
                td["_owner_name"] = oa["owner"].name
                all_alive.append(td)
    all_alive.sort(key=lambda t: -t["projected"])
    mvp_teams = all_alive[:5]

    # Biggest upsets so far (lowest seed that won)
    upsets = []
    for g in completed_games:
        if g.winner and g.team1 and g.team2:
            loser = g.team1 if g.winner_id == g.team2_id else g.team2
            seed_diff = g.winner.seed - loser.seed
            if seed_diff > 0:  # Higher seed number = worse team won
                upsets.append({
                    "winner": g.winner.name,
                    "winner_seed": g.winner.seed,
                    "loser": loser.name,
                    "loser_seed": loser.seed,
                    "round": g.round_name,
                    "owner": g.winner.owner.name,
                    "seed_diff": seed_diff,
                })
    upsets.sort(key=lambda u: -u["seed_diff"])

    total_actual = sum(o["actual_winnings"] for o in owner_analytics)

    # Compute total possible pot
    total_pot_possible = 0
    for rn, prize in ROUND_PRIZES.items():
        if rn == "First Four":
            continue
        expected_games = {"Round of 64": 32, "Round of 32": 16, "Sweet 16": 8,
                          "Elite 8": 4, "Final Four": 2, "Championship": 1}
        total_pot_possible += prize * expected_games.get(rn, 0)

    return {
        "owners": owner_analytics,
        "total_pot": round(total_actual, 2),
        "total_pot_possible": total_pot_possible,
        "games_played": len(completed_games),
        "games_with_odds": games_with_odds,
        "teams_with_futures": teams_with_futures,
        "total_games": len(all_games),
        "rooting_guide": rooting_guide,
        "round_breakdown": round_breakdown,
        "mvp_teams": mvp_teams,
        "upsets": upsets[:5],
    }
