"""Scoring and leaderboard calculations."""

from sqlalchemy.orm import Session

from app.models import Owner, Team, Game
from app.config import ROUND_PRIZES, ROUND_ORDER


def get_leaderboard(db: Session) -> list[dict]:
    """Calculate leaderboard with wins and winnings per owner."""
    owners = db.query(Owner).all()
    leaderboard = []

    for owner in owners:
        team_ids = [t.id for t in owner.teams]
        wins_by_round = {}
        total_wins = 0
        total_winnings = 0

        for round_name in ROUND_ORDER:
            prize = ROUND_PRIZES[round_name]
            wins = (
                db.query(Game)
                .filter(
                    Game.winner_id.in_(team_ids),
                    Game.round_name == round_name,
                    Game.status == "final",
                )
                .count()
            )
            wins_by_round[round_name] = wins
            total_wins += wins
            total_winnings += wins * prize

        active_teams = sum(1 for t in owner.teams if not t.eliminated)

        leaderboard.append({
            "owner": owner,
            "total_wins": total_wins,
            "total_winnings": total_winnings,
            "wins_by_round": wins_by_round,
            "active_teams": active_teams,
            "total_teams": len(owner.teams),
        })

    leaderboard.sort(key=lambda x: (-x["total_winnings"], -x["total_wins"]))
    return leaderboard


def get_owner_detail(db: Session, owner_id: int) -> dict | None:
    """Get detailed stats for a single owner."""
    owner = db.query(Owner).filter(Owner.id == owner_id).first()
    if not owner:
        return None

    teams_detail = []
    for team in owner.teams:
        wins = (
            db.query(Game)
            .filter(Game.winner_id == team.id, Game.status == "final")
            .all()
        )
        winnings = sum(ROUND_PRIZES.get(g.round_name, 0) for g in wins)
        teams_detail.append({
            "team": team,
            "wins": len(wins),
            "winnings": winnings,
            "games": wins,
        })

    teams_detail.sort(key=lambda x: (-x["wins"], -x["winnings"]))

    total_wins = sum(t["wins"] for t in teams_detail)
    total_winnings = sum(t["winnings"] for t in teams_detail)

    return {
        "owner": owner,
        "teams": teams_detail,
        "total_wins": total_wins,
        "total_winnings": total_winnings,
        "active_teams": sum(1 for t in owner.teams if not t.eliminated),
    }
