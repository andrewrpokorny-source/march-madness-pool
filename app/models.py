from sqlalchemy import Column, Integer, Float, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship

from app.database import Base


class Owner(Base):
    __tablename__ = "owners"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    teams = relationship("Team", back_populates="owner")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    seed = Column(Integer, nullable=False)
    region = Column(String, nullable=True)
    espn_id = Column(String, nullable=True, unique=True)
    espn_logo_url = Column(String, nullable=True)
    eliminated = Column(Boolean, default=False)
    is_playin = Column(Boolean, default=False)
    playin_label = Column(String, nullable=True)  # e.g. "Texas/NC State"

    owner_id = Column(Integer, ForeignKey("owners.id"), nullable=False)
    owner = relationship("Owner", back_populates="teams")

    wins_as_team1 = relationship("Game", foreign_keys="Game.winner_id", back_populates="winner")


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    espn_game_id = Column(String, unique=True, nullable=True)
    round_name = Column(String, nullable=False)
    region = Column(String, nullable=True)
    game_date = Column(DateTime, nullable=True)

    team1_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team2_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    score1 = Column(Integer, nullable=True)
    score2 = Column(Integer, nullable=True)
    winner_id = Column(Integer, ForeignKey("teams.id"), nullable=True)

    status = Column(String, default="scheduled")  # scheduled, in_progress, final

    # DraftKings implied win probabilities (vig-removed)
    team1_win_prob = Column(Float, nullable=True)
    team2_win_prob = Column(Float, nullable=True)
    spread = Column(Float, nullable=True)  # from team1 perspective

    team1 = relationship("Team", foreign_keys=[team1_id])
    team2 = relationship("Team", foreign_keys=[team2_id])
    winner = relationship("Team", foreign_keys=[winner_id], back_populates="wins_as_team1")
