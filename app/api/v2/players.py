"""bancho.py's v2 apis for interacting with players"""

from __future__ import annotations

import dataclasses
from typing import Annotated
from typing import Literal

from fastapi import APIRouter, Depends, status, Request  # Request 추가
from fastapi.datastructures import UploadFile
from fastapi.param_functions import File
from fastapi.param_functions import Query

from app.api import dependencies as api_dependencies
from app.api.v2.common import actors
from app.api.v2.common import responses
from app.api.v2.common.parameters import GameModeParam
from app.api.v2.common.responses import Failure
from app.api.v2.common.responses import Success
from app.api.v2.models.maps import MostPlayedMap
from app.api.v2.models.players import PasswordUpdate
from app.api.v2.models.players import Player
from app.api.v2.models.players import PlayerStats
from app.api.v2.models.players import PlayerStatus
from app.api.v2.models.players import ProfileUpdate
from app.api.v2.models.players import SearchPlayer
from app.api.v2.models.players import Notification
from app.api.v2.models.scores import PlayerScore
from app.api.v2.models.scores import ScoreBeatmap
from app.constants.gamemodes import GameMode
from app.repositories.users import User
from app.services.account_settings import AccountSettingsService
from app.services.account_settings import PasswordChangeResultCode
from app.services.avatars import MAX_AVATAR_SIZE_BYTES
from app.services.avatars import AvatarsService
from app.services.avatars import AvatarUploadResultCode
from app.services.favourites import FavouritesService
from app.services.players import PlayersService
from app.services.relationships import AddFriendResult
from app.services.relationships import RelationshipsService
from app.services.scores import ScoresService
from app.services.visibility import can_view_player

router = APIRouter()


@router.get("/players")
async def get_players(
    *,
    priv: int | None = None,
    country: str | None = None,
    clan_id: int | None = None,
    clan_priv: int | None = None,
    preferred_mode: int | None = None,
    play_style: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[list[Player]] | Failure:
    listing = await players_service.fetch_players(
        priv=priv,
        country=country,
        clan_id=clan_id,
        clan_priv=clan_priv,
        preferred_mode=preferred_mode,
        play_style=play_style,
        page=page,
        page_size=page_size,
        viewer=actor,
    )

    response = [Player.model_validate(rec) for rec in listing.players]

    return responses.success(
        content=response,
        meta={
            "total": listing.total_players,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/players/search")
async def search_players(
    *,
    query: str = Query(..., alias="q", min_length=2, max_length=32),
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[list[SearchPlayer]] | Failure:
    # staff see hidden players, and players can always find themselves
    players = await players_service.search_players(query, viewer=actor)

    response = [SearchPlayer.model_validate(rec) for rec in players]
    return responses.success(
        content=response,
        meta={"total": len(response)},
    )


@router.get("/players/{player_id_or_name}")
async def get_player(
    player_id_or_name: str,
    key: Literal["id", "username"] | None = None,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[Player] | Failure:
    # `key` forces how the identifier is interpreted (usernames may be
    # all digits, shadowed by the id namespace); left unspecified,
    # numeric identifiers are treated as ids.
    if key == "username":
        interpret_as_id = False
    elif key == "id":
        interpret_as_id = True
    else:
        interpret_as_id = player_id_or_name.isdecimal()

    if interpret_as_id:
        if not player_id_or_name.isdecimal():
            return responses.failure(
                message="Player not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        data = await players_service.fetch_player(int(player_id_or_name))
    else:
        data = await players_service.fetch_player_by_id_or_name(
            user_id=None,
            username=player_id_or_name,
        )
    if data is None or not can_view_player(
        viewer=actor,
        target_id=data.id,
        target_priv=data.priv,
    ):
        # hidden (restricted or unverified) players are reported as
        # missing to everyone but staff and themselves
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    response = Player.model_validate(data)
    return responses.success(response)


@router.put("/players/{player_id}/avatar")
async def update_player_avatar(
    player_id: int,
    avatar_file: UploadFile = File(...),
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    avatars_service: Annotated[
        AvatarsService,
        Depends(api_dependencies.get_avatars_service),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only update your own avatar.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    result = await avatars_service.upload_avatar(
        user_id=actor.id,
        avatar_data=await avatar_file.read(),
    )
    if result is AvatarUploadResultCode.FILE_TOO_LARGE:
        max_mb = MAX_AVATAR_SIZE_BYTES // (1024 * 1024)
        return responses.failure(
            message=f"Avatar file too large (max {max_mb}MB).",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if result is AvatarUploadResultCode.INVALID_FILE_TYPE:
        return responses.failure(
            message="Avatars must be png or jpeg images.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return responses.success(None)


from pathlib import Path
from fastapi.responses import FileResponse

BANNERS_PATH = Path.cwd() / ".data/banners"
BANNERS_PATH.mkdir(parents=True, exist_ok=True)
MAX_BANNER_SIZE_BYTES = 4 * 1024 * 1024

@router.get("/players/{player_id}/banner")
async def get_player_banner(player_id: int):
    for ext in ("png", "jpg", "jpeg", "gif"):
        banner_file = BANNERS_PATH / f"{player_id}.{ext}"
        if banner_file.exists():
            return FileResponse(banner_file)
    return responses.failure(
        message="Banner not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )

@router.put("/players/{player_id}/banner")
async def update_player_banner(
    player_id: int,
    banner_file: UploadFile = File(...),
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only update your own banner.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    banner_data = await banner_file.read()
    with memoryview(banner_data) as banner_view:
        if len(banner_view) > MAX_BANNER_SIZE_BYTES:
            return responses.failure(
                message="Banner file too large (max 4MB).",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        import app.utils
        if app.utils.has_jpeg_headers_and_trailers(banner_view):
            extension = "jpeg"
        elif app.utils.has_png_headers_and_trailers(banner_view):
            extension = "png"
        else:
            return responses.failure(
                message="Banners must be png or jpeg images.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    for ext in ("png", "jpg", "jpeg", "gif"):
        if ext != extension:
            (BANNERS_PATH / f"{player_id}.{ext}").unlink(missing_ok=True)

    (BANNERS_PATH / f"{player_id}.{extension}").write_bytes(banner_data)
    return responses.success(None)


@router.get("/players/{player_id}/friends")
async def get_player_friends(
    player_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    relationships_service: Annotated[
        RelationshipsService,
        Depends(api_dependencies.get_relationships_service),
    ],
) -> Success[list[Player]] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only view your own friends.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    friends = await relationships_service.fetch_friends(actor)
    response = [Player.model_validate(rec) for rec in friends]
    return responses.success(response, meta={"total": len(response)})


@router.get("/players/{player_id}/following")
async def get_player_following(
    player_id: int,
    *,
    relationships_service: Annotated[
        RelationshipsService,
        Depends(api_dependencies.get_relationships_service),
    ],
) -> Success[list[Player]] | Failure:
    following = await relationships_service.fetch_following(player_id)
    response = [Player.model_validate(rec) for rec in following]
    return responses.success(response, meta={"total": len(response)})


@router.get("/players/{player_id}/followers")
async def get_player_followers(
    player_id: int,
    *,
    relationships_service: Annotated[
        RelationshipsService,
        Depends(api_dependencies.get_relationships_service),
    ],
) -> Success[list[Player]] | Failure:
    followers = await relationships_service.fetch_followers(player_id)
    response = [Player.model_validate(rec) for rec in followers]
    return responses.success(response, meta={"total": len(response)})


@router.get("/notifications")
async def get_notifications(
    actor: Annotated[User | None, Depends(actors.get_optional_actor)],
) -> Success[list[Notification]] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    import app.state
    rows = await app.state.services.database.fetch_all(
        "SELECT id, user_id, type, title, content, link, is_read, created_at "
        "FROM notifications WHERE user_id = :user_id "
        "ORDER BY created_at DESC LIMIT 50",
        {"user_id": actor.id}
    )
    response = []
    for row in rows:
        notif_dict = dict(row)
        notif_dict["is_read"] = bool(notif_dict["is_read"])
        response.append(Notification.model_validate(notif_dict))
    return responses.success(response, meta={"total": len(response)})


@router.post("/notifications/read_all")
async def read_all_notifications(
    actor: Annotated[User | None, Depends(actors.get_optional_actor)],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    import app.state
    await app.state.services.database.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = :user_id",
        {"user_id": actor.id}
    )
    return responses.success(None)


@router.post("/notifications/{notification_id}/read")
async def read_notification(
    notification_id: int,
    actor: Annotated[User | None, Depends(actors.get_optional_actor)],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    import app.state
    notif = await app.state.services.database.fetch_one(
        "SELECT user_id FROM notifications WHERE id = :id",
        {"id": notification_id}
    )
    if notif is None:
        return responses.failure(
            message="Notification not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if notif["user_id"] != actor.id:
        return responses.failure(
            message="Forbidden.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
        
    await app.state.services.database.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = :id",
        {"id": notification_id}
    )
    return responses.success(None)


@router.put("/players/{player_id}/friends/{target_id}")
async def add_player_friend(
    player_id: int,
    target_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    relationships_service: Annotated[
        RelationshipsService,
        Depends(api_dependencies.get_relationships_service),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only manage your own friends.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    result = await relationships_service.add_friend(actor, target_id)
    if result is AddFriendResult.CANNOT_FRIEND_SELF:
        return responses.failure(
            message="You cannot friend yourself.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if result is AddFriendResult.TARGET_NOT_FOUND:
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return responses.success(None)


@router.delete("/players/{player_id}/friends/{target_id}")
async def remove_player_friend(
    player_id: int,
    target_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    relationships_service: Annotated[
        RelationshipsService,
        Depends(api_dependencies.get_relationships_service),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only manage your own friends.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await relationships_service.remove_friend(actor.id, target_id)
    return responses.success(None)


@router.get("/players/{player_id}/favourites")
async def get_player_favourites(
    player_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
    favourites_service: Annotated[
        FavouritesService,
        Depends(api_dependencies.get_favourites_service),
    ],
) -> Success[list[int]] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    set_ids = await favourites_service.fetch_favourite_set_ids(player_id)
    return responses.success(set_ids, meta={"total": len(set_ids)})


@router.put("/players/{player_id}/favourites/{set_id}")
async def add_player_favourite(
    player_id: int,
    set_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    favourites_service: Annotated[
        FavouritesService,
        Depends(api_dependencies.get_favourites_service),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only manage your own favourites.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # adding an existing favourite is a no-op rather than an error
    await favourites_service.add_favourite(player_id=actor.id, map_set_id=set_id)
    return responses.success(None)


@router.delete("/players/{player_id}/favourites/{set_id}")
async def remove_player_favourite(
    player_id: int,
    set_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    favourites_service: Annotated[
        FavouritesService,
        Depends(api_dependencies.get_favourites_service),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only manage your own favourites.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await favourites_service.remove_favourite(player_id=actor.id, map_set_id=set_id)
    return responses.success(None)


@router.patch("/players/{player_id}")
async def update_player_profile(
    player_id: int,
    args: ProfileUpdate,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    account_settings_service: Annotated[
        AccountSettingsService,
        Depends(api_dependencies.get_account_settings_service),
    ],
) -> Success[Player] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only update your own profile.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    errors = await account_settings_service.validate_profile_update(
        actor,
        username=args.username,
        country=args.country,
        userpage_content=args.userpage_content,
    )
    if errors:
        message = " ".join(
            error for field_errors in errors.values() for error in field_errors
        )
        return responses.failure(
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    updated_user = await account_settings_service.update_profile(
        actor,
        username=args.username,
        country=args.country,
        preferred_mode=args.preferred_mode,
        userpage_content=args.userpage_content,
    )

    response = Player.model_validate(updated_user)
    return responses.success(response)


@router.put("/players/{player_id}/password")
async def update_player_password(
    player_id: int,
    args: PasswordUpdate,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    account_settings_service: Annotated[
        AccountSettingsService,
        Depends(api_dependencies.get_account_settings_service),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if actor.id != player_id:
        return responses.failure(
            message="You may only change your own password.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    result = await account_settings_service.change_password(
        actor,
        current_password=args.current_password,
        new_password=args.new_password,
    )
    if result.code is PasswordChangeResultCode.INCORRECT_CURRENT_PASSWORD:
        return responses.failure(
            message="Incorrect current password.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if result.code is PasswordChangeResultCode.VALIDATION_FAILED:
        assert result.errors is not None
        return responses.failure(
            message=" ".join(result.errors),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return responses.success(None)


@router.get("/players/{player_id}/status")
async def get_player_status(
    player_id: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[PlayerStatus] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player status not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    status_data = players_service.fetch_player_status(player_id)
    if status_data is None:
        return responses.failure(
            message="Player status not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    response = PlayerStatus(
        login_time=status_data.login_time,
        action=status_data.action,
        info_text=status_data.info_text,
        mode=status_data.mode,
        mods=status_data.mods,
        beatmap_id=status_data.beatmap_id,
    )
    return responses.success(response)


@router.get("/players/{player_id}/stats/{mode}")
async def get_player_mode_stats(
    player_id: int,
    mode: int,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[PlayerStats] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    data = await players_service.fetch_player_mode_stats_with_ranks(
        player_id=player_id,
        mode=mode,
        country=player.country,
    )
    if data is None:
        return responses.failure(
            message="Player stats not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    response = PlayerStats.model_validate(data)
    return responses.success(response)


@router.get("/players/{player_id}/stats")
async def get_player_stats(
    player_id: int,
    *,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[list[PlayerStats]] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    listing = await players_service.fetch_player_stats_with_ranks(
        player_id=player_id,
        country=player.country,
        page=page,
        page_size=page_size,
    )

    response = [PlayerStats.model_validate(rec) for rec in listing.stats]

    return responses.success(
        response,
        meta={
            "total": listing.total_stats,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/players/{player_id}/scores")
async def get_player_scores(
    player_id: int,
    *,
    scope: Literal["best", "recent"] = "best",
    mode: GameModeParam = Query(0),
    limit: int = Query(25, ge=1, le=100),
    include_loved: bool = False,
    include_failed: bool = True,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
    scores_service: Annotated[
        ScoresService,
        Depends(api_dependencies.get_scores_service),
    ],
) -> Success[list[PlayerScore]] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    scores = await scores_service.fetch_player_scores(
        player_id=player_id,
        mode=GameMode(mode),
        mods=None,
        strong_mods_equality=True,
        scope=scope,
        limit=limit,
        include_loved=include_loved,
        include_failed=include_failed,
    )

    response = [
        PlayerScore.model_validate(
            {
                **dataclasses.asdict(row.score),
                "beatmap": (
                    ScoreBeatmap.model_validate(row.beatmap)
                    if row.beatmap is not None
                    else None
                ),
            },
        )
        for row in scores
    ]

    return responses.success(
        content=response,
        meta={
            "total": len(response),
            "scope": scope,
            "mode": mode,
        },
    )


@router.get("/players/{player_id}/most_played")
async def get_player_most_played(
    player_id: int,
    *,
    mode: GameModeParam = Query(0),
    limit: int = Query(25, ge=1, le=100),
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
    scores_service: Annotated[
        ScoresService,
        Depends(api_dependencies.get_scores_service),
    ],
) -> Success[list[MostPlayedMap]] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    maps = await scores_service.fetch_player_most_played(
        player_id=player_id,
        mode=GameMode(mode),
        limit=limit,
    )

    response = [MostPlayedMap.model_validate(rec) for rec in maps]
    return responses.success(
        content=response,
        meta={
            "total": len(response),
            "mode": mode,
        },
    )


@router.get("/players/{player_id}/first_places")
async def get_player_first_places(
    player_id: int,
    *,
    mode: GameModeParam = Query(0),
    limit: int = Query(50, ge=1, le=100),
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
    players_service: Annotated[
        PlayersService,
        Depends(api_dependencies.get_players_service),
    ],
) -> Success[list[PlayerScore]] | Failure:
    player = await players_service.fetch_player(player_id)
    if player is None or not can_view_player(
        viewer=actor,
        target_id=player.id,
        target_priv=player.priv,
    ):
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    import app.state

    # Find all scores by this player that are #1 on their respective map
    # A score is #1 if no other score on the same map_md5 + mode has higher pp
    rows = await app.state.services.database.fetch_all(
        """
        SELECT s.id, s.map_md5, s.userid, s.score, s.pp, s.acc,
               s.max_combo, s.mods, s.n300, s.n100, s.n50, s.nmiss,
               s.ngeki, s.nkatu, s.grade, s.status AS score_status,
               s.mode, s.play_time, s.time_elapsed, s.perfect,
               m.id AS map_id, m.set_id, m.md5, m.status AS map_status,
               m.artist, m.title, m.version, m.creator,
               m.last_update, m.total_length, m.max_combo AS map_max_combo,
               m.plays, m.passes, m.mode AS map_mode,
               m.bpm, m.cs, m.ar, m.od, m.hp, m.diff
        FROM scores s
        JOIN maps m ON s.map_md5 = m.md5
        WHERE s.userid = :player_id
          AND s.mode = :mode
          AND s.status = 2
          AND s.pp = (
              SELECT MAX(s2.pp)
              FROM scores s2
              WHERE s2.map_md5 = s.map_md5
                AND s2.mode = s.mode
                AND s2.status = 2
          )
        ORDER BY s.pp DESC
        LIMIT :limit
        """,
        {"player_id": player_id, "mode": mode, "limit": limit},
    )

    response = []
    for row in rows:
        row_dict = dict(row)
        score_data = {
            "id": row_dict["id"],
            "map_md5": row_dict["map_md5"],
            "userid": row_dict["userid"],
            "score": row_dict["score"],
            "pp": row_dict["pp"],
            "acc": row_dict["acc"],
            "max_combo": row_dict["max_combo"],
            "mods": row_dict["mods"],
            "n300": row_dict["n300"],
            "n100": row_dict["n100"],
            "n50": row_dict["n50"],
            "nmiss": row_dict["nmiss"],
            "ngeki": row_dict["ngeki"],
            "nkatu": row_dict["nkatu"],
            "grade": row_dict["grade"],
            "status": row_dict["score_status"],
            "mode": row_dict["mode"],
            "play_time": row_dict["play_time"],
            "time_elapsed": row_dict["time_elapsed"],
            "perfect": row_dict["perfect"],
            "beatmap": ScoreBeatmap.model_validate({
                "id": row_dict["map_id"],
                "set_id": row_dict["set_id"],
                "md5": row_dict["md5"],
                "status": row_dict["map_status"],
                "artist": row_dict["artist"],
                "title": row_dict["title"],
                "version": row_dict["version"],
                "creator": row_dict["creator"],
                "last_update": row_dict["last_update"],
                "total_length": row_dict["total_length"],
                "max_combo": row_dict["map_max_combo"],
                "plays": row_dict["plays"],
                "passes": row_dict["passes"],
                "mode": row_dict["map_mode"],
                "bpm": row_dict["bpm"],
                "cs": row_dict["cs"],
                "ar": row_dict["ar"],
                "od": row_dict["od"],
                "hp": row_dict["hp"],
                "diff": row_dict["diff"],
            }),
        }
        response.append(PlayerScore.model_validate(score_data))

    return responses.success(
        content=response,
        meta={
            "total": len(response),
            "mode": mode,
        },
    )


from pydantic import BaseModel

class AdminPlayerUpdate(BaseModel):
    name: str | None = None
    priv: int | None = None
    country: str | None = None

@router.patch("/admin/players/{player_id}")
async def admin_update_player(
    player_id: int,
    args: AdminPlayerUpdate,
    *,
    actor: Annotated[
        User | None,
        Depends(actors.get_optional_actor),
    ],
) -> Success[None] | Failure:
    if actor is None:
        return responses.failure(
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    from app.constants.privileges import Privileges
    if not (actor.priv & (Privileges.ADMINISTRATOR | Privileges.DEVELOPER)):
        return responses.failure(
            message="You do not have permission to perform this action.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # bancho.py의 전역 app 모듈을 함수 내부에서 직접 임포트합니다.
    import app

    db_player = await app.state.services.database.fetch_one(
        "SELECT name, priv, country FROM users WHERE id = :id",
        {"id": player_id}
    )
    if db_player is None:
        return responses.failure(
            message="Player not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    updates = {}
    if args.name is not None and args.name.strip():
        name = args.name.strip()
        updates["name"] = name
        updates["safe_name"] = name.lower().replace(" ", "_")
    if args.priv is not None:
        updates["priv"] = args.priv
    if args.country is not None and args.country.strip():
        updates["country"] = args.country.strip().lower()

    if not updates:
        return responses.failure(
            message="No fields to update.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    set_clause = ", ".join(f"{k} = :{k}" for k in updates.keys())
    await app.state.services.database.execute(
        f"UPDATE users SET {set_clause} WHERE id = :id",
        {**updates, "id": player_id}
    )

    online_player = app.state.sessions.players.get(id=player_id)
    if online_player is not None:
        if "name" in updates:
            online_player.name = updates["name"]
        if "priv" in updates:
            online_player.priv = Privileges(updates["priv"])
        if "country" in updates:
            online_player.geoloc["country"]["acronym"] = updates["country"]

    return responses.success(None)