from __future__ import annotations

from datetime import datetime

from . import BaseModel

# input models


# output models


class Map(BaseModel):
    id: int
    server: str
    set_id: int
    status: int
    md5: str
    artist: str
    title: str
    version: str
    creator: str
    filename: str
    last_update: datetime
    total_length: int
    max_combo: int
    frozen: bool
    plays: int
    passes: int
    mode: int
    bpm: float
    cs: float
    ar: float
    od: float
    hp: float
    diff: float
    max_pp: int = 0
    theoretical_max_pp: int = 0


class MostPlayedMap(BaseModel):
    id: int
    set_id: int
    md5: str
    status: int
    artist: str
    title: str
    version: str
    creator: str

    # the number of times the player has played the map
    plays: int


class MapRating(BaseModel):
    average: float | None
    count: int


class MapSetDifficulty(BaseModel):
    id: int
    cs: float
    diff: float
    version: str


class MapSet(BaseModel):
    id: int
    server: str
    artist: str
    title: str
    creator: str
    last_update: datetime
    total_plays: int
    min_diff: float
    max_diff: float
    diffs_count: int
    max_pp: int
    theoretical_max_pp: int
    cs: float = 0
    players_count: int = 0
    difficulties: list[MapSetDifficulty] = []
