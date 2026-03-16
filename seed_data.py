"""Seed the database with owners and their drafted teams."""

from app.database import engine, SessionLocal, Base
from app.models import Owner, Team

DRAFT = {
    "Esther": [
        (1, "Michigan"),
        (4, "Kansas"),
        (6, "Tennessee"),
        (9, "Utah State"),
        (10, "Santa Clara"),
        (11, "South Florida"),
        (12, "McNeese"),
        (16, "Siena"),
    ],
    "Jim": [
        (1, "Arizona"),
        (3, "Michigan State"),
        (4, "Alabama"),
        (8, "Villanova"),
        (11, "VCU"),
        (13, "Troy"),
        (15, "Queens"),
        (15, "Furman"),
    ],
    "Posey": [
        (1, "Duke"),
        (3, "Gonzaga"),
        (5, "Texas Tech"),
        (7, "St. Mary's"),
        (7, "Kentucky"),
        (12, "N Iowa"),
        (13, "Hawaii"),
        (16, "Prairie View/Lehigh"),
    ],
    "Matthew": [
        (1, "Florida"),
        (3, "Illinois"),
        (6, "Louisville"),
        (9, "Iowa"),
        (10, "Missouri"),
        (11, "Texas/NC State"),
        (13, "Hofstra"),
        (16, "Howard/UMBC"),
    ],
    "Brittany": [
        (2, "Houston"),
        (4, "Nebraska"),
        (5, "Vanderbilt"),
        (7, "UCLA"),
        (11, "Miami/SMU"),
        (12, "High Point"),
        (14, "Penn"),
        (15, "Idaho"),
    ],
    "Andrew": [
        (2, "Iowa State"),
        (5, "St. John's"),
        (5, "Wisconsin"),
        (8, "OSU"),
        (8, "Georgia"),
        (10, "Texas A&M"),
        (13, "Cal Baptist"),
        (15, "Tennessee State"),
    ],
    "Michael": [
        (2, "UConn"),
        (2, "Purdue"),
        (6, "BYU"),
        (7, "Miami"),
        (8, "Clemson"),
        (9, "St. Louis"),
        (14, "Wright State"),
        (14, "N Dakota State"),
    ],
    "Brenda": [
        (3, "Virginia"),
        (4, "Arkansas"),
        (6, "UNC"),
        (9, "TCU"),
        (10, "UCF"),
        (12, "Akron"),
        (14, "Kennesaw State"),
        (16, "LIU"),
    ],
}

# Play-in teams are indicated by "/" in the name
PLAYIN_TEAMS = {"Prairie View/Lehigh", "Texas/NC State", "Miami/SMU", "Howard/UMBC"}


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Check if already seeded
    if db.query(Owner).first():
        print("Database already seeded. Skipping.")
        db.close()
        return

    for owner_name, teams in DRAFT.items():
        owner = Owner(name=owner_name)
        db.add(owner)
        db.flush()

        for seed, team_name in teams:
            is_playin = team_name in PLAYIN_TEAMS
            team = Team(
                name=team_name,
                seed=seed,
                owner_id=owner.id,
                is_playin=is_playin,
                playin_label=team_name if is_playin else None,
            )
            db.add(team)

    db.commit()
    print(f"Seeded {len(DRAFT)} owners with their teams.")
    db.close()


if __name__ == "__main__":
    seed()
