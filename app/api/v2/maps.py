"""bancho.py's v2 apis for interacting with maps"""

from __future__ import annotations

import dataclasses
from typing import Annotated
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from fastapi.param_functions import Query

from app.api import dependencies as api_dependencies
from app.api.v2.common import responses
from app.api.v2.common.parameters import GameModeParam
from app.api.v2.common.responses import Failure
from app.api.v2.common.responses import Success
from app.api.v2.models.maps import Map, MapRating, MapSet
from app.api.v2.models.scores import MapScore
from app.api.v2.models.scores import ScorePlayer
from app.constants.gamemodes import GameMode
from app.services.maps import BeatmapRatingService
from app.services.maps import MapsService
from app.services.scores import ScoresService

router = APIRouter()


import app.state
from datetime import datetime
from akatsuki_pp_py import Beatmap as CalcBeatmap, Calculator as CalcCalculator
import os

_max_pp_cache: dict[tuple[int, int], int] = {}

def get_real_theoretical_max_pp(map_id: int, mode: int, diff: float) -> int:
    cache_key = (map_id, mode)
    if cache_key in _max_pp_cache:
        return _max_pp_cache[cache_key]
        
    osu_file_path = f".data/osu/{map_id}.osu"
    if os.path.exists(osu_file_path):
        try:
            calc_bmap = CalcBeatmap(path=osu_file_path)
            calculator = CalcCalculator(
                mode=mode,
                acc=100.0,
            )
            result = calculator.performance(calc_bmap)
            val = int(round(result.pp))
            _max_pp_cache[cache_key] = val
            return val
        except Exception:
            pass
            
    # Fallback to the power-law estimate
    return int(round(8.0 * (diff ** 2.2)))


@router.get("/maps/keys")
async def get_maps_keys() -> Success[list[int]] | Failure:
    rows = await app.state.services.database.fetch_all(
        "SELECT DISTINCT cs FROM maps WHERE cs > 0 ORDER BY cs ASC"
    )
    keys = [int(row["cs"]) for row in rows]
    return responses.success(keys)

@router.get("/mapsets")
async def get_mapsets(
    *,
    status: int | None = None,
    mode: int | None = None,
    query: str | None = None,
    keys: int | None = None,
    sort: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> Success[list[MapSet]] | Failure:
    query_parts = []
    params = {}

    if status is not None:
        query_parts.append("m.status = :status")
        params["status"] = status
    if mode is not None:
        query_parts.append("m.mode = :mode")
        params["mode"] = mode

    if query and query.strip():
        query_parts.append("(m.title LIKE :search_query OR m.artist LIKE :search_query OR m.creator LIKE :search_query OR m.filename LIKE :search_query)")
        params["search_query"] = f"%{query.strip()}%"

    if keys is not None:
        query_parts.append("m.cs = :keys")
        params["keys"] = keys

    where_clause = " AND ".join(query_parts) if query_parts else "1=1"

    order_by = "total_plays DESC"
    if sort == "newest":
        order_by = "max_id DESC"
    elif sort == "diff_asc":
        order_by = "min_diff ASC"
    elif sort == "diff_desc":
        order_by = "max_diff DESC"
    elif sort == "players_desc":
        order_by = "players_count DESC"
    elif sort == "max_pp_desc":
        order_by = "max_pp DESC"
    elif sort == "theoretical_max_pp_desc":
        order_by = "max_diff DESC"

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_row = await app.state.services.database.fetch_one(
        f"SELECT COUNT(DISTINCT m.set_id) AS total FROM maps m WHERE {where_clause}",
        {k: v for k, v in params.items() if k not in ("limit", "offset")}
    )
    total_mapsets = count_row["total"] if count_row else 0

    sql = f"""
        SELECT m.set_id AS id,
               MAX(m.id) AS max_id,
               m.server,
               m.artist,
               m.title,
               m.creator,
               MAX(m.last_update) AS last_update,
               SUM(m.plays) AS total_plays,
               MIN(m.diff) AS min_diff,
               MAX(m.diff) AS max_diff,
               COUNT(m.id) AS diffs_count,
               MAX(m.cs) AS cs,
               GROUP_CONCAT(CONCAT(m.id, '::', m.cs, '::', m.diff, '::', m.mode, '::', m.version) ORDER BY m.diff ASC SEPARATOR '|||') AS diffs,
               COALESCE((
                   SELECT COUNT(DISTINCT s.userid) 
                   FROM scores s 
                   JOIN maps m2 ON s.map_md5 = m2.md5 
                   WHERE m2.set_id = m.set_id
               ), 0) AS players_count,
               COALESCE((
                   SELECT MAX(pp) 
                   FROM scores s 
                   JOIN maps m2 ON s.map_md5 = m2.md5 
                   WHERE m2.set_id = m.set_id AND s.status = 2
               ), 0) AS max_pp
        FROM maps m
        WHERE {where_clause}
        GROUP BY m.set_id, m.server, m.artist, m.title, m.creator
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
    """
    
    db_mapsets = await app.state.services.database.fetch_all(sql, params)
    
    response = []
    for row in db_mapsets:
        set_dict = dict(row)
        if isinstance(set_dict["last_update"], str):
            clean_date = set_dict["last_update"].replace("Z", "+00:00")
            set_dict["last_update"] = datetime.fromisoformat(clean_date)
            
        diffs = []
        if set_dict.get("diffs"):
            for item in set_dict["diffs"].split("|||"):
                parts = item.split("::", 4)
                if len(parts) == 5:
                    try:
                        diffs.append({
                            "id": int(parts[0]),
                            "cs": float(parts[1]),
                            "diff": float(parts[2]),
                            "mode": int(parts[3]),
                            "version": parts[4]
                        })
                    except (ValueError, TypeError):
                        continue
        set_dict["difficulties"] = diffs

        max_pp = int(round(set_dict["max_pp"]))
        
        # Calculate the real theoretical max PP from the difficulties
        theoretical_max_pp = 0
        for d in diffs:
            real_pp = get_real_theoretical_max_pp(d["id"], d["mode"], d["diff"])
            if real_pp > theoretical_max_pp:
                theoretical_max_pp = real_pp
                
        if theoretical_max_pp == 0:
            # Fallback if no difficulties
            theoretical_max_pp = int(round(8.0 * (set_dict["max_diff"] ** 2.2)))

        # Still make sure we don't display a value lower than the highest achieved max_pp
        theoretical_max_pp = max(theoretical_max_pp, max_pp)
        
        set_dict["max_pp"] = max_pp
        set_dict["theoretical_max_pp"] = theoretical_max_pp
        set_dict["players_count"] = int(round(set_dict.get("players_count", 0)))
        
        response.append(MapSet.model_validate(set_dict))

    return responses.success(
        content=response,
        meta={
            "total": total_mapsets,
            "page": page,
            "page_size": page_size,
        },
    )

@router.get("/maps")
async def get_maps(
    *,
    set_id: int | None = None,
    server: str | None = None,
    status: int | None = None,
    artist: str | None = None,
    creator: str | None = None,
    filename: str | None = None,
    mode: int | None = None,
    frozen: bool | None = None,
    query: str | None = None,
    keys: int | None = None,
    sort: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> Success[list[Map]] | Failure:
    query_parts = []
    params = {}

    if set_id is not None:
        query_parts.append("m.set_id = :set_id")
        params["set_id"] = set_id
    if server is not None:
        query_parts.append("m.server = :server")
        params["server"] = server
    if status is not None:
        query_parts.append("m.status = :status")
        params["status"] = status
    if artist is not None:
        query_parts.append("m.artist = :artist")
        params["artist"] = artist
    if creator is not None:
        query_parts.append("m.creator = :creator")
        params["creator"] = creator
    if filename is not None:
        query_parts.append("m.filename = :filename")
        params["filename"] = filename
    if mode is not None:
        query_parts.append("m.mode = :mode")
        params["mode"] = mode
    if frozen is not None:
        query_parts.append("m.frozen = :frozen")
        params["frozen"] = frozen

    if query and query.strip():
        query_parts.append("(m.title LIKE :search_query OR m.artist LIKE :search_query OR m.creator LIKE :search_query OR m.filename LIKE :search_query)")
        params["search_query"] = f"%{query.strip()}%"

    if keys is not None:
        query_parts.append("m.cs = :keys")
        params["keys"] = keys

    where_clause = " AND ".join(query_parts) if query_parts else "1=1"

    order_by = "m.plays DESC"
    if sort == "newest":
        order_by = "m.id DESC"
    elif sort == "diff_asc":
        order_by = "m.diff ASC"
    elif sort == "diff_desc":
        order_by = "m.diff DESC"
    elif sort == "max_pp_asc":
        order_by = "max_pp ASC"
    elif sort == "max_pp_desc":
        order_by = "max_pp DESC"
    elif sort == "theoretical_max_pp_asc":
        order_by = "m.diff ASC"
    elif sort == "theoretical_max_pp_desc":
        order_by = "m.diff DESC"

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    count_row = await app.state.services.database.fetch_one(
        f"SELECT COUNT(*) AS total FROM maps m WHERE {where_clause}",
        count_params
    )
    total_maps = count_row["total"] if count_row else 0

    sql = f"""
        SELECT m.*, 
               COALESCE((SELECT MAX(pp) FROM scores s WHERE s.map_md5 = m.md5 AND s.status = 2), 0) AS max_pp
        FROM maps m
        WHERE {where_clause}
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
    """
    
    db_maps = await app.state.services.database.fetch_all(sql, params)
    
    response = []
    for row in db_maps:
        map_dict = dict(row)
        if isinstance(map_dict["last_update"], str):
            clean_date = map_dict["last_update"].replace("Z", "+00:00")
            map_dict["last_update"] = datetime.fromisoformat(clean_date)
            
        diff = map_dict["diff"]
        theoretical_max_pp = get_real_theoretical_max_pp(map_dict["id"], map_dict["mode"], diff)
        
        max_pp = int(round(map_dict["max_pp"]))
        theoretical_max_pp = max(theoretical_max_pp, max_pp)
        
        map_dict["max_pp"] = max_pp
        map_dict["theoretical_max_pp"] = theoretical_max_pp
        
        response.append(Map.model_validate(map_dict))

    return responses.success(
        content=response,
        meta={
            "total": total_maps,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/maps/{map_id}")
async def get_map(
    map_id: int,
    maps_service: Annotated[
        MapsService,
        Depends(api_dependencies.get_maps_service),
    ],
) -> Success[Map] | Failure:
    data = await maps_service.fetch_map(map_id)
    if data is None:
        return responses.failure(
            message="Map not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    response = Map.model_validate(data)
    return responses.success(response)


@router.get("/maps/{map_id}/rating")
async def get_map_rating(
    map_id: int,
    maps_service: Annotated[
        MapsService,
        Depends(api_dependencies.get_maps_service),
    ],
    beatmap_rating_service: Annotated[
        BeatmapRatingService,
        Depends(api_dependencies.get_beatmap_rating_service),
    ],
) -> Success[MapRating] | Failure:
    beatmap = await maps_service.fetch_map(map_id)
    if beatmap is None:
        return responses.failure(
            message="Map not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    stats = await beatmap_rating_service.fetch_map_rating_stats(beatmap.md5)
    response = MapRating(average=stats.average, count=stats.count)
    return responses.success(response)


@router.get("/maps/{map_id}/scores")
async def get_map_scores(
    map_id: int,
    *,
    scope: Literal["best", "recent"] = "best",
    mode: GameModeParam = Query(0),
    limit: int = Query(50, ge=1, le=100),
    maps_service: Annotated[
        MapsService,
        Depends(api_dependencies.get_maps_service),
    ],
    scores_service: Annotated[
        ScoresService,
        Depends(api_dependencies.get_scores_service),
    ],
) -> Success[list[MapScore]] | Failure:
    bmap = await maps_service.fetch_map(map_id)
    if bmap is None:
        return responses.failure(
            message="Map not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    scores = await scores_service.fetch_map_scores(
        map_md5=bmap.md5,
        mode=GameMode(mode),
        mods=None,
        strong_mods_equality=True,
        scope=scope,
        limit=limit,
    )

    response = [
        MapScore.model_validate(
            {
                **dataclasses.asdict(rec),
                "player": ScorePlayer(
                    id=rec.userid,
                    name=rec.player_name,
                    country=rec.player_country,
                    clan_id=rec.clan_id,
                    clan_name=rec.clan_name,
                    clan_tag=rec.clan_tag,
                ),
            },
        )
        for rec in scores
    ]

    return responses.success(
        content=response,
        meta={
            "total": len(response),
            "scope": scope,
            "mode": mode,
        },
    )
