"""bancho.py's v2 apis for interacting with overall server state"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends

from app.api import dependencies as api_dependencies
from app.api.v2.common import responses
from app.api.v2.common.responses import Failure
from app.api.v2.common.responses import Success
from app.api.v2.models.server import ServerStats
from app.services.players import PlayersService

router = APIRouter()


import app.state

@router.get("/server/stats")
async def get_server_stats(
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[ServerStats] | Failure:
    max_pp_row = await app.state.services.database.fetch_one(
        "SELECT s.pp, s.userid, u.name, m.id AS map_id, m.set_id "
        "FROM scores s "
        "JOIN users u ON s.userid = u.id "
        "JOIN maps m ON s.map_md5 = m.md5 "
        "WHERE s.status = 2 AND u.priv & 1 "
        "ORDER BY s.pp DESC "
        "LIMIT 1"
    )
    
    if max_pp_row and max_pp_row["pp"] is not None:
        max_pp = int(round(max_pp_row["pp"]))
        max_pp_player_id = max_pp_row["userid"]
        max_pp_player_name = max_pp_row["name"]
        max_pp_map_id = max_pp_row["map_id"]
        max_pp_map_set_id = max_pp_row["set_id"]
    else:
        max_pp = 0
        max_pp_player_id = None
        max_pp_player_name = None
        max_pp_map_id = None
        max_pp_map_set_id = None

    total_plays_row = await app.state.services.database.fetch_one(
        "SELECT SUM(plays) AS total_plays FROM stats WHERE id != 1"
    )
    total_plays = int(total_plays_row["total_plays"]) if total_plays_row and total_plays_row["total_plays"] is not None else 0

    response = ServerStats(
        online_players=players_service.fetch_online_player_count(),
        total_players=max(0, await players_service.fetch_total_player_count() - 1),
        max_pp=max_pp,
        max_pp_player_id=max_pp_player_id,
        max_pp_player_name=max_pp_player_name,
        max_pp_map_id=max_pp_map_id,
        max_pp_map_set_id=max_pp_map_set_id,
        total_plays=total_plays,
    )
    return responses.success(response)


from pydantic import BaseModel

class OnlinePlayerHistoryEntry(BaseModel):
    time: int
    score_id: int
    map_id: int
    set_id: int
    artist: str
    title: str
    version: str
    creator: str
    mode: int
    mods: int
    acc: float
    pp: float
    passed: bool
    grade: str

class OnlinePlayerInfo(BaseModel):
    id: int
    name: str
    country: str
    action: int
    info_text: str
    mode: int
    mods: int
    map_id: int
    session_play_count: int
    session_play_history: list[OnlinePlayerHistoryEntry]

@router.get("/server/online_players")
async def get_online_players() -> Success[list[OnlinePlayerInfo]]:
    import app.state
    response = []
    for player in app.state.sessions.players:
        if player.is_bot_client:
            continue
            
        history = []
        for entry in getattr(player, "session_play_history", []):
            history.append(
                OnlinePlayerHistoryEntry(
                    time=entry["time"],
                    score_id=entry["score_id"],
                    map_id=entry["map_id"],
                    set_id=entry["set_id"],
                    artist=entry["artist"],
                    title=entry["title"],
                    version=entry["version"],
                    creator=entry["creator"],
                    mode=entry["mode"],
                    mods=entry["mods"],
                    acc=entry["acc"],
                    pp=entry["pp"],
                    passed=entry["passed"],
                    grade=entry["grade"],
                )
            )
            
        response.append(
            OnlinePlayerInfo(
                id=player.id,
                name=player.name,
                country=player.geoloc["country"]["acronym"],
                action=int(player.status.action),
                info_text=player.status.info_text,
                mode=int(player.status.mode),
                mods=int(player.status.mods),
                map_id=player.status.map_id,
                session_play_count=getattr(player, "session_play_count", 0),
                session_play_history=history,
            )
        )
    return responses.success(response)
