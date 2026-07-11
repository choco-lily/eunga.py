from __future__ import annotations

from . import BaseModel

# input models


# output models


class ServerStats(BaseModel):
    online_players: int
    total_players: int
    max_pp: int
    max_pp_player_id: int | None
    max_pp_player_name: str | None
    max_pp_map_id: int | None
    max_pp_map_set_id: int | None
    total_plays: int
