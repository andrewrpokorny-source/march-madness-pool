"""Migration: split play-in team pairs into individual team records."""

from app.database import SessionLocal
from app.models import Team, Game

# Old combined name -> (new_name_1, new_name_2)
SPLITS = {
    "Prairie View/Lehigh": ("Prairie View", "Lehigh"),
    "Texas/NC State": ("Texas", "NC State"),
    "Miami/SMU": ("Miami", "SMU"),
    "Howard/UMBC": ("Howard", "UMBC"),
}


def migrate():
    db = SessionLocal()

    # 1. Delete all games (will be re-synced from ESPN)
    db.query(Game).delete()
    print("Deleted all games for fresh sync.")

    # 2. Clear espn_ids on all teams
    for t in db.query(Team).all():
        t.espn_id = None
        t.espn_logo_url = None

    # 3. Split play-in teams
    for old_name, (name1, name2) in SPLITS.items():
        old_team = db.query(Team).filter(Team.name == old_name).first()
        if not old_team:
            print(f"  Skipping {old_name} (not found)")
            continue

        owner_id = old_team.owner_id
        seed = old_team.seed
        playin_label = old_name

        # Rename existing record to first team
        old_team.name = name1
        old_team.playin_label = playin_label
        old_team.is_playin = True
        print(f"  Renamed {old_name} -> {name1} (owner_id={owner_id})")

        # Create second team
        new_team = Team(
            name=name2,
            seed=seed,
            owner_id=owner_id,
            is_playin=True,
            playin_label=playin_label,
        )
        db.add(new_team)
        print(f"  Created {name2} (owner_id={owner_id})")

    # 4. Rename Michael's "Miami" to "Miami OH"
    miami_michael = (
        db.query(Team)
        .filter(Team.name == "Miami", Team.seed == 7)
        .first()
    )
    if miami_michael:
        miami_michael.name = "Miami OH"
        print(f"  Renamed Miami -> Miami OH (Michael's team)")

    db.commit()
    print("Migration complete. Run a sync to reload games.")
    db.close()


if __name__ == "__main__":
    migrate()
