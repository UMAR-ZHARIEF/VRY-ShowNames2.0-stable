import asyncio
import os
import socket
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3
from colr import color as colr
from InquirerPy import inquirer
from rich.console import Console as RichConsole

from src.colors import Colors
from src.config import Config
from src.configurator import configure
from src.console_guard import install_console_guard
from src.constants import (
    AGENTCOLORLIST,
    GAMEPOD_REGION_MAP,
    NUMBERTORANKS,
    PARTYICONLIST,
    SHORT_NUMBERTORANKS,
    before_ascendant_seasons,
    color,
    gamemodes,
    hide_names,
    version,
)
from src.content import Content
from src.errors import Error
from src.Loadouts import Loadouts
from src.logs import Logging
from src.names import Names
from src.name_cache import NameCache
from src.player_stats import PlayerStats
from src.presences import Presences
from src.rank import Rank
from src.requestsV import Requests
from src.rpc import Rpc
from src.rpc_payload import RpcOverrides
from src.server import Server
from src.single_instance import acquire_single_instance_lock, release_single_instance_lock
from src.states.coregame import Coregame
from src.states.menu import Menu
from src.states.pregame import Pregame
from src.stats import Stats
from src.table import Table
from src.websocket import Ws
from src.enemy_probes import enemy_snapshot, start_loadouts_probe_safe
from src.enemy_table_data import resolve_enemy_names, build_enemy_rows
from src import row_builder
from src.match_context import MatchContext
from src import static_content
from src.state_polling import derive_game_state, next_poll_seconds, pregame_draw_decision, render_signature, should_declare_disconnected, should_redraw
from src.os import get_os

# Render-on-change: fingerprint of the last drawn output. A cycle whose
# Server / lobby display state, draw-gate state, and the other per-session
# mutables now live in the MatchContext object (ctx), created below.


def enemy_level_for(match_id, puuid):
    key = (match_id, puuid)
    if key in ctx.enemy_level_cache:
        return ctx.enemy_level_cache[key]
    level = None
    try:
        level = pstats.get_level_with_fallback(puuid)
    except Exception:
        level = None
    if not isinstance(level, int) or level <= 0:
        level = None
    ctx.enemy_level_cache[key] = level
    return level




urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

os.system(f"title VRY ShowNames v{version}")

# Single-instance guard: a second copy exits immediately instead of
# fighting the first one over the console and the log files.
if not acquire_single_instance_lock():
    print("VRY ShowNames is already running (another window has it open).")
    print("Close that copy first, then start this one again.")
    os._exit(0)
# The lock stays held for the whole session; src.single_instance
# registers its own release on interpreter shutdown (atexit).


def program_exit(status: int):  # so we don't need to import the entire sys module
    log(f"exited program with error code {status}")
    raise sys.exit(status)


def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # doesn't even have to be reachable
        s.connect(("10.254.254.254", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


try:
    Logging = Logging()
    log = Logging.log

    # OS Logging
    log(f"Operating system: {get_os()}\n")

    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--config":
            configure()
            run_app = inquirer.confirm(
                message="Do you want to run vRY now?", default=True
            ).execute()
            if run_app:
                os.system("cls")
            else:
                os._exit(0)
        else:
            os.system("cls")
    except Exception as e:
        print("Something went wrong while running configurator!")
        log(f"configurator encountered an error")
        log(str(traceback.format_exc()))
        input("press enter to exit...\n")
        os._exit(1)

    ErrorSRC = Error(log)

    Requests = Requests(version, log, ErrorSRC)

    cfg = Config(log)

    content = Content(Requests, log)

    rank = Rank(Requests, log, content, before_ascendant_seasons)
    pstats = PlayerStats(Requests, log, cfg)

    nameCache = NameCache(log)
    namesClass = Names(Requests, log, cfg)

    # Name-cache warm-up walks the last 25 matches and is pure cache filling,
    # so it runs in a daemon thread and never blocks startup (measured 5-6s).
    _bootstrap_names_started = threading.Event()

    def _bootstrap_names_background():
        if _bootstrap_names_started.is_set():
            return
        _bootstrap_names_started.set()
        try:
            namesClass.bootstrap_from_match_history(
                Requests.puuid, nameCache, player_stats=pstats, max_matches=25
            )
            log("bootstrap: finished in background")
        except Exception as e:
            log(f"bootstrap: skipped due to error: {e}")

    threading.Thread(
        target=_bootstrap_names_background, daemon=True, name="names-bootstrap"
    ).start()

    presences = Presences(Requests, log)

    menu = Menu(Requests, log, presences)
    pregame = Pregame(Requests, log)
    coregame = Coregame(Requests, log)

    Server = Server(log, ErrorSRC)
    Server.start_server()

    agent_dict = content.get_all_agents()

    map_info = content.get_all_maps()
    map_urls = content.get_map_urls(map_info)
    map_splashes = content.get_map_splashes(map_info)

    current_map = coregame.get_current_map(map_urls, map_splashes)

    colors = Colors(hide_names, agent_dict, AGENTCOLORLIST)

    loadoutsClass = Loadouts(Requests, log, colors, Server, current_map)
    table = Table(cfg, log)

    stats = Stats()

    if cfg.get_feature_flag("discord_rpc"):
        rpc_overrides = RpcOverrides.from_config(cfg, log, getattr(colors, "agent_dict", None), map_urls)
        rpc = Rpc(map_urls, gamemodes, colors, log, rpc_overrides)
    else:
        rpc = None

    Wss = Ws(Requests.lockfile, Requests, cfg, colors, hide_names, Server, rpc, nameCache)

    log(f"VRY ShowNames v{version}")

    valoApiSkins = None
    for _skins_attempt in range(3):
        valoApiSkins = static_content.get_json("/v1/weapons/skins")
        if valoApiSkins is not None:
            break
        log("valorant-api weapons/skins fetch failed, retrying...")
        time.sleep(2)
    if valoApiSkins is None:
        # matches today's behavior: a failed fetch aborted startup
        raise RuntimeError("could not fetch https://valorant-api.com/v1/weapons/skins")
    gameContent = content.get_content()
    if gameContent is None:
        # fetch() returns None on remote transport failures; seasonID=None
        # is the same "missing season" state get_previous_season_id already
        # produces, and get_rank/get_act_episode_from_act_id degrade to the
        # default rank data instead of crashing.
        log("content fetch failed: skipping season lookup, rank data will use defaults")
        seasonID = None
        previousSeasonID = None
    else:
        seasonID = content.get_latest_season_id(gameContent)
        previousSeasonID = content.get_previous_season_id(gameContent)
    # Rank+stats cache per player for the current match now lives in ctx
    # (MatchContext.reset/ctx.ensure_match_player_cache).
    lastGameState = ""
    # Serializes match_player_cache bookkeeping inside
    # get_or_fetch_rank_and_stats: the parallel pre-fetch workers call it
    # concurrently, and ctx.ensure_match_player_cache sweeps the cache dict
    # while other workers may be inserting into it. Network fetches stay
    # outside the lock; sequential callers hit an uncontended lock, so cache
    # contents and TTL rules are unchanged.
    _rank_stats_cache_lock = threading.Lock()

    def get_or_fetch_rank_and_stats(player_subject, current_match_id):
        if current_match_id:
            with _rank_stats_cache_lock:
                ctx.ensure_match_player_cache(current_match_id)
                cached = ctx.match_player_cache["players"].get(player_subject)
                if cached is not None:
                    return (
                        cached["playerRank"],
                        cached["previousPlayerRank"],
                        cached["ppstats"],
                    )
        playerRank = rank.get_rank(player_subject, seasonID)
        previousPlayerRank = rank.get_rank(player_subject, previousSeasonID)
        ppstats = pstats.get_stats(player_subject)
        if current_match_id:
            with _rank_stats_cache_lock:
                if ctx.match_player_cache["match_id"] == current_match_id:
                    ctx.match_player_cache["players"][player_subject] = {
                        "playerRank": dict(playerRank) if isinstance(playerRank, dict) else playerRank,
                        "previousPlayerRank": dict(previousPlayerRank) if isinstance(previousPlayerRank, dict) else previousPlayerRank,
                        "ppstats": dict(ppstats) if isinstance(ppstats, dict) else ppstats,
                        "ts": time.time(),
                    }
        return playerRank, previousPlayerRank, ppstats

    print("\nvRY Mobile", color(f"- {get_ip()}:{cfg.port}", fore=(255, 127, 80)))

    print(
        color(
            "\nVisit https://vry.netlify.app/matchLoadouts to view full player inventories\n",
            fore=(255, 253, 205),
        )
    )

    # Screen writes must never kill the app: once installed, a broken
    # console pipe (OSError WinError 232/233) degrades to log-only output
    # instead of reaching the top-level handler below.
    install_console_guard(log)
    richConsole = RichConsole()

    ctx = MatchContext()

    def get_menus_rank_and_stats(subject):
        cached = ctx.menus_stats_get(subject)
        if cached is not None:
            return cached
        playerRank = rank.get_rank(subject, seasonID)
        previousPlayerRank = rank.get_rank(subject, previousSeasonID)
        ppstats = pstats.get_stats(subject)
        ctx.menus_stats_set(subject, playerRank, previousPlayerRank, ppstats)
        return playerRank, previousPlayerRank, ppstats

    def prefetch_players_parallel(subjects, fetch_one):
        # Runs one fetch job per player concurrently (bounded pool, <=8
        # workers) so the per-player gather phases stop being serial. Results
        # are consumed in submission order, so the first failing player's
        # exception propagates exactly like the sequential loop it replaced.
        # Callers keep rendering sequentially from the warmed caches, so
        # table bytes, row order and spinner text are unchanged.
        subjects = list(dict.fromkeys(subjects))
        if not subjects:
            return
        max_workers = min(8, len(subjects))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(fetch_one, subject) for subject in subjects]
            for future in futures:
                future.result()

    firstTime = True
    firstPrint = True
    while True:
        table.clear()
        table.set_default_field_names()
        table.reset_runtime_col_flags()

        # check if short ranks should be used
        if cfg.get_feature_flag("short_ranks"):
            Ranks = SHORT_NUMBERTORANKS
        else:
            Ranks = NUMBERTORANKS

        try:

            if firstTime:
                run = True
                while run:
                    presence = presences.get_presence()
                    private_presence = presences.get_private_presence(presence)
                    # wait until your own valorant presence is initialized
                    if private_presence is not None:
                        if cfg.get_feature_flag("discord_rpc"):
                            rpc.set_rpc(private_presence)
                        game_state = presences.get_game_state(presence)
                        if game_state is not None:
                            run = False
                    time.sleep(2)
                log(f"first game state: {game_state}")
            else:
                previous_game_state = game_state
                if game_state != previous_game_state or ctx.state_entered_at == 0.0:
                    ctx.state_entered_at = time.time()
                if cfg.get_feature_flag("game_chat"):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    game_state = loop.run_until_complete(
                        Wss.recconect_to_websocket(game_state)
                    )
                    ctx.consecutive_state_failures = 0
                else:
                    # Adaptive tolerant polling: no long-lived connection; each
                    # check is independent and a failure keeps the previous
                    # state (see ctx.consecutive_state_failures above).
                    time.sleep(next_poll_seconds(game_state, cfg.cooldown))
                    presence = presences.get_presence()
                    private_presence = None
                    if presence is not None:
                        private_presence = presences.get_private_presence(presence)
                    # Refresh the Discord card presence every poll cycle (the
                    # websocket handler does this when game_chat is on).
                    if private_presence is not None:
                        if cfg.get_feature_flag("discord_rpc"):
                            rpc.set_rpc(private_presence)
                    _state_now = derive_game_state(private_presence)
                    if _state_now is not None:
                        game_state = _state_now
                        ctx.consecutive_state_failures = 0
                    else:
                        ctx.consecutive_state_failures += 1
                        log(
                            f"state poll failed "
                            f"({ctx.consecutive_state_failures}/3), keeping {game_state}"
                        )
                state_changed = previous_game_state != game_state
                # We invalidate the cached responses when going from any state to menus
                if state_changed and game_state == "MENUS":
                    rank.invalidate_cached_responses()
                    ctx.reset_match_player_cache()
                    if hasattr(pstats, "clear_runtime_cache"):
                        pstats.clear_runtime_cache()
                    # Retroactive post-match name lookup for any unresolved incognito players
                    if ctx.pending_post_match_lookup is not None:
                        _match_id, _blank_puuids = ctx.pending_post_match_lookup
                        namesClass.retroactive_post_match_lookup(_blank_puuids, _match_id, nameCache)
                        nameCache.save_now()
                        ctx.pending_post_match_lookup = None
                if state_changed:
                    log(f"new game state: {game_state}")
                if cfg.get_feature_flag("game_chat"):
                    loop.close()
            firstTime = False
            if game_state is not None:
                ctx.consecutive_state_failures = 0
        except TypeError:
            ctx.consecutive_state_failures += 1
            if should_declare_disconnected(ctx.consecutive_state_failures):
                game_state = "DISCONNECTED"
                ctx.reset_match_player_cache()
                if hasattr(pstats, "clear_runtime_cache"):
                    pstats.clear_runtime_cache()
            else:
                log(
                    f"state check failed "
                    f"({ctx.consecutive_state_failures}/3), keeping {game_state}"
                )

        if game_state == "DISCONNECTED":
            richConsole.print("[yellow]Disconnected from Valorant. Attempting to reconnect...[/yellow]")
            # Loop waits for the Valorant client to respond
            while True:
                # Rereads the lockfile
                Requests.lockfile = Requests.get_lockfile()

                if Requests.lockfile is None:
                    time.sleep(5)
                    continue

                presence_check = presences.get_presence()
                
                if presence_check is not None:
                    break 
                
                time.sleep(5)

            richConsole.print("[green]Reconnected successfully! Loading...[/green]")
            
            Requests.get_headers(refresh=True)

            Wss = Ws(Requests.lockfile, Requests, cfg, colors, hide_names, Server, rpc, nameCache)

            firstTime = True
            lastGameState = ""
            ctx.reset_match_player_cache()
            if hasattr(pstats, "clear_runtime_cache"):
                pstats.clear_runtime_cache()
            continue

        if True:
            log(f"getting new {game_state} scoreboard")
            lastGameState = game_state
            game_state_dict = {
                "INGAME": color("In-Game", fore=(241, 39, 39)),
                "PREGAME": color("Agent Select", fore=(103, 237, 76)),
                "MENUS": color("In-Menus", fore=(238, 241, 54)),
            }

            if (not firstPrint) and cfg.get_feature_flag("pre_cls"):
                os.system("cls")

            is_leaderboard_needed = False
            
            # get new presence
            presence = presences.get_presence()
            priv_presence = presences.get_private_presence(presence)
            
            # Temp fix: Riot is swapping between nested and flat API structures.
            # The private presence can decode to None right at a match boundary,
            # which used to raise TypeError and kill the app (seen in logs
            # log-171/226/552). Keep the previous ctx.gamemode for this cycle.
            party_state = ""
            if not isinstance(priv_presence, dict):
                log("private presence unavailable at match boundary, keeping previous mode")
            else:
                if "partyPresenceData" in priv_presence: # Check for nested structure
                    party_state = priv_presence["partyPresenceData"]["partyState"]
                elif "partyState" in priv_presence: # Check for flattened structure
                    party_state = priv_presence["partyState"]
                else:
                    # No known structure found, log and fail
                    log("ERROR: Unknown presence API structure in 'main'.")
                    party_state = priv_presence["partyPresenceData"]["partyState"]

                if (
                    priv_presence["provisioningFlow"] == "CustomGame"
                    or party_state == "CUSTOM_GAME_SETUP"
                ):
                    ctx.gamemode = "Custom Game"
                else:
                    ctx.gamemode = gamemodes.get(priv_presence["queueId"])

            heartbeat_data = {
                "time": int(time.time()),
                "state": game_state,
                "mode": ctx.gamemode,
                "puuid": Requests.puuid,
                "players": {},
            }

            if game_state == "INGAME":
                coregame_stats = coregame.get_coregame_stats()
                # Riot's coregame service has provisioning lag — when
                # PREGAME ends, /core-game/v1/players/{puuid} can return
                # 404 for several seconds before becoming available.
                # Retry instead of skipping the render entirely (which
                # would leave the table blank for the whole match).
                _ingame_retry = 0
                while coregame_stats is None and _ingame_retry < 5:
                    time.sleep(2)
                    coregame_stats = coregame.get_coregame_stats()
                    _ingame_retry += 1
                    log(f"INGAME coregame still 404, retry {_ingame_retry}/5")
                if coregame_stats is None:
                    log("INGAME coregame never came online after "
                        "60s of retries; skipping render")
                    continue
                coregame_match_id = coregame.get_coregame_match_id()
                ctx.ensure_match_player_cache(coregame_match_id)
                Players = coregame_stats["Players"]
                # data for chat to function
                partyMembers = menu.get_party_members(Requests.puuid, presence)
                partyMembersList = [a["Subject"] for a in partyMembers]

                players_data = {}
                players_data.update({"ignore": partyMembersList})
                for player in Players:
                    if player["Subject"] == Requests.puuid:
                        if cfg.get_feature_flag("discord_rpc"):
                            rpc.set_data({"agent": player["CharacterID"]})
                    players_data.update(
                        {
                            player["Subject"]: {
                                "team": player["TeamID"],
                                "agent": player["CharacterID"],
                                "streamer_mode": player["PlayerIdentity"]["Incognito"],
                            }
                        }
                    )
                Wss.set_player_data(players_data)

                ctx.server = coregame_stats.get("GamePodID", "")
                presences.wait_for_presence(namesClass.get_players_puuid(Players))
                names = namesClass.get_names_from_puuids(Players)
                names = namesClass.patch_hidden_names(names, presence)
                _pre = sum(1 for _, _n in names.items() if not _n.split("#")[0])
                names = namesClass.resolve_aggressive(
                    names,
                    name_cache=nameCache,
                    presence_list=presence,
                    match_id=coregame_match_id,
                    game_state="INGAME",
                    own_puuid=Requests.puuid,
                )
                _post = sum(1 for _, _n in names.items() if not _n.split("#")[0])
                log(f"INGAME resolve: blanks {_pre} -> {_post}")
                # Save resolved names to persistent cache
                namesClass.update_cache(names, nameCache, source="api")
                # Patch any still-blank names from persistent cache
                names = namesClass.patch_from_cache(names, nameCache)
                nameCache.save_now()
                # Track blank puuids for post-match retroactive lookup
                pending_blank_puuids = [p for p, n in names.items() if not n.split("#")[0]]
                if pending_blank_puuids:
                    ctx.pending_post_match_lookup = (coregame_match_id, pending_blank_puuids)
                # Pre-fetch stats for all players (concurrently; the render
                # loop below reads the warmed match cache unchanged)
                prefetch_players_parallel(
                    [_p["Subject"] for _p in Players],
                    lambda _subject: get_or_fetch_rank_and_stats(
                        _subject, coregame_match_id
                    ),
                )
                # Pre-warm the hidden-level lookups for exactly the players
                # the render loop would fetch (HideAccountLevel, not self,
                # not party) so its in-loop calls stay cache hits.
                prefetch_players_parallel(
                    [
                        _p["Subject"]
                        for _p in Players
                        if _p["PlayerIdentity"]["HideAccountLevel"]
                        and _p["Subject"] != Requests.puuid
                        and _p["Subject"] not in partyMembersList
                    ],
                    pstats.get_level_with_fallback,
                )
                loadouts_arr = loadoutsClass.get_match_loadouts(
                    coregame_match_id,
                    Players,
                    cfg.weapon,
                    valoApiSkins,
                    names,
                    state="game",
                )
                loadouts = loadouts_arr[0]
                phantom_loadouts = loadouts_arr[1]
                loadouts_data = loadouts_arr[2]
                isRange = False
                playersLoaded = 1

                heartbeat_data["map"] = (map_urls[coregame_stats["MapID"].lower()],)
                with richConsole.status("Loading Players...") as status:
                    partyOBJ = menu.get_party_json(
                        namesClass.get_players_puuid(Players), presence
                    )
                    # log(f"retrieved names dict: {names}")
                    Players.sort(
                        key=lambda Players: Players["PlayerIdentity"].get(
                            "AccountLevel"
                        ),
                        reverse=True,
                    )
                    Players.sort(key=lambda Players: Players["TeamID"], reverse=True)
                    partyCount = 0
                    partyNum = 0
                    partyIcons = {}
                    lastTeamBoolean = False
                    lastTeam = "Red"

                    already_played_with = []
                    stats_data = stats.read_data()
                    names_list = []

                    for p in Players:
                        if p["Subject"] == Requests.puuid:
                            allyTeam = p["TeamID"]
                    for player in Players:
                        status.update(
                            f"Loading players... [{playersLoaded}/{len(Players)}]"
                        )
                        playersLoaded += 1

                        if player["Subject"] in stats_data.keys():
                            if (
                                player["Subject"] != Requests.puuid
                                and player["Subject"] not in partyMembersList
                            ):
                                curr_player_stat = stats_data[player["Subject"]][-1]
                                i = 1
                                while (
                                    curr_player_stat["match_id"] == coregame.match_id
                                    and len(stats_data[player["Subject"]]) > i
                                ):
                                    i += 1
                                    curr_player_stat = stats_data[player["Subject"]][-i]
                                if curr_player_stat["match_id"] != coregame.match_id:
                                    # checking for party members and self players
                                    times = 0
                                    m_set = ()
                                    for m in stats_data[player["Subject"]]:
                                        if (
                                            m["match_id"] != coregame.match_id
                                            and m["match_id"] not in m_set
                                        ):
                                            times += 1
                                            m_set += (m["match_id"],)
                                    
                                    already_played_with.append({
                                        "times": times,
                                        "name": names[player["Subject"]],
                                        "agent": curr_player_stat["agent"],
                                        "time_diff": time.time() - curr_player_stat["epoch"]
                                    })

                        party_icon = ""
                        # set party premade icon
                        for party in partyOBJ:
                            if player["Subject"] in partyOBJ[party]:
                                if party not in partyIcons:
                                    partyIcons.update(
                                        {party: PARTYICONLIST[partyCount]}
                                    )
                                    # PARTY_ICON
                                    party_icon = PARTYICONLIST[partyCount]
                                    partyNum = partyCount + 1
                                    partyCount += 1
                                else:
                                    # PARTY_ICON
                                    party_icon = partyIcons[party]
                        playerRank, previousPlayerRank, ppstats = get_or_fetch_rank_and_stats(
                            player["Subject"], coregame_match_id
                        )

                        if player["Subject"] == Requests.puuid:
                            if cfg.get_feature_flag("discord_rpc"):
                                rpc.set_data(
                                    {
                                        "rank": playerRank["rank"],
                                        "rank_name": colors.escape_ansi(
                                            NUMBERTORANKS[playerRank["rank"]]
                                        )
                                        + " | "
                                        + str(playerRank["rr"])
                                        + "rr",
                                    }
                                )

                        kd = ppstats["kd"]
                        perf_bonus = row_builder.gradient_cell(ppstats, "perf_bonus", colors.get_perf_bonus_color)

                        ranked_rating_earned = row_builder.rr_earned_cell(ppstats, colors)

                        player_level = player["PlayerIdentity"].get("AccountLevel")

                        if player["PlayerIdentity"]["Incognito"]:
                            Namecolor = colors.get_color_from_team(
                                player["TeamID"],
                                names[player["Subject"]],
                                player["Subject"],
                                Requests.puuid,
                                agent=player["CharacterID"],
                                party_members=partyMembersList,
                            )
                        else:
                            Namecolor = colors.get_color_from_team(
                                player["TeamID"],
                                names[player["Subject"]],
                                player["Subject"],
                                Requests.puuid,
                                party_members=partyMembersList,
                            )
                        if lastTeam != player["TeamID"]:
                            if lastTeamBoolean:
                                table.add_empty_row()
                        lastTeam = player["TeamID"]
                        lastTeamBoolean = True
                        if player["PlayerIdentity"]["HideAccountLevel"]:
                            if (
                                player["Subject"] == Requests.puuid
                                or player["Subject"] in partyMembersList
                            ):
                                PLcolor = colors.level_to_color(player_level)
                            else:
                                _real_level = pstats.get_level_with_fallback(player["Subject"])
                                if _real_level:
                                    PLcolor = colors.level_to_color(_real_level)
                                else:
                                    PLcolor = "?"
                        else:
                            PLcolor = colors.level_to_color(player_level)
                        # AGENT
                        # agent = str(agent_dict.get(player["CharacterID"].lower()))
                        agent = colors.get_agent_from_uuid(
                            player["CharacterID"].lower()
                        )
                        if agent == "" and len(Players) == 1:
                            isRange = True

                        # NAME
                        name = Namecolor

                        # skin
                        skin = loadouts.get(player["Subject"], "")
                        phantom_skin = phantom_loadouts.get(player["Subject"], "")

                        # RANK
                        rankName = row_builder.rank_cell(playerRank, Ranks, cfg, cfg.table)

                        # RANK RATING
                        rr = playerRank["rr"]

                        peakRankAct = row_builder.peak_act_suffix(playerRank, cfg)

                        # PEAK RANK
                        peakRank = Ranks[playerRank["peakrank"]] + peakRankAct

                        # PREVIOUS RANK
                        previousRank = Ranks[previousPlayerRank["rank"]]

                        # LEADERBOARD
                        leaderboard = playerRank["leaderboard"]

                        hs = row_builder.gradient_cell(ppstats, "hs", colors.get_hs_gradient)
                        wr = row_builder.wr_cell(playerRank, colors)

                        if int(leaderboard) > 0:
                            is_leaderboard_needed = True

                        # LEVEL
                        level = PLcolor
                        table.add_row_table(
                            [
                                party_icon,
                                agent,
                                name,
                                skin,
                                phantom_skin,
                                rankName,
                                rr,
                                peakRank,
                                previousRank,
                                leaderboard,
                                hs,
                                wr,
                                kd,
                                level,
                                ranked_rating_earned,
                                perf_bonus,
                            ]
                        )
                        names_list.append(name)

                        heartbeat_data["players"][player["Subject"]] = {
                            "puuid": player["Subject"],
                            "name": names[player["Subject"]],
                            "partyNumber": partyNum if party_icon != "" else 0,
                            "agent": agent_dict.get(player["CharacterID"].lower(), "Unknown"),
                            "rank": playerRank["rank"],
                            "peakRank": playerRank["peakrank"],
                            "peakRankAct": peakRankAct,
                            "rr": rr,
                            "kd": ppstats["kd"],
                            "headshotPercentage": ppstats["hs"],
                            "winPercentage": f"{playerRank['wr']} ({playerRank['numberofgames']})",
                            "level": player_level,
                            "agentImgLink": loadouts_data["Players"][
                                player["Subject"]
                            ].get("Agent", None),
                            "team": loadouts_data["Players"][player["Subject"]].get(
                                "Team", None
                            ),
                            "sprays": loadouts_data["Players"][player["Subject"]].get(
                                "Sprays", None
                            ),
                            "title": loadouts_data["Players"][player["Subject"]].get(
                                "Title", None
                            ),
                            "playerCard": loadouts_data["Players"][
                                player["Subject"]
                            ].get("PlayerCard", None),
                            "weapons": loadouts_data["Players"][player["Subject"]].get(
                                "Weapons", None
                            ),
                        }

                        stats.save_data(
                            {
                                player["Subject"]: {
                                    "name": names[player["Subject"]],
                                    "agent": agent_dict.get(player["CharacterID"].lower(), "Unknown"),
                                    "map": current_map,
                                    "rank": playerRank["rank"],
                                    "rr": rr,
                                    "match_id": coregame.match_id,
                                    "epoch": time.time(),
                                }
                            }
                        )
                        # bar()
            elif game_state == "PREGAME":
                already_played_with = []
                pregame_stats = pregame.get_pregame_stats()
                if pregame_stats == None:
                    continue
                ctx.server = pregame_stats.get("GamePodID", "")
                Players = pregame_stats["AllyTeam"]["Players"]
                presences.wait_for_presence(namesClass.get_players_puuid(Players))
                names = namesClass.get_names_from_puuids(Players)
                names = namesClass.patch_hidden_names(names, presence)
                pregame_match_id = pregame_stats.get("ID")
                if cfg.get_feature_flag("enemy_probe_v3") and pregame_match_id and pregame_match_id not in ctx.v3_probed_matches:
                    ctx.v3_probed_matches.add(pregame_match_id)
                    try:
                        from src.enemy_probe_v3 import probe_v3_main_async, coregame_race_async
                        ally_puuids = [p["Subject"] for p in Players if p.get("Subject")]
                        probe_v3_main_async(Requests, pregame_match_id, Requests.puuid,
                                            ally_puuids, log)
                        coregame_race_async(Requests, pregame_match_id, ally_puuids,
                                            Requests.puuid, log, budget_seconds=70)
                    except Exception as _v3e:
                        log(f"[v3] dispatch exc: {type(_v3e).__name__}: {_v3e}")
                if cfg.get_feature_flag("enemy_loadouts_probe") and pregame_match_id:
                    start_loadouts_probe_safe(Requests, pregame_match_id, [p["Subject"] for p in Players if p.get("Subject")], log)
                _pre = sum(1 for _, _n in names.items() if not _n.split("#")[0])
                names = namesClass.resolve_aggressive(
                    names,
                    name_cache=nameCache,
                    presence_list=presence,
                    match_id=pregame_match_id,
                    game_state="PREGAME",
                    own_puuid=Requests.puuid,
                )
                _post = sum(1 for _, _n in names.items() if not _n.split("#")[0])
                log(f"PREGAME resolve: blanks {_pre} -> {_post}")
                # Save resolved names to persistent cache
                namesClass.update_cache(names, nameCache, source="api")
                # Patch any still-blank names from persistent cache
                names = namesClass.patch_from_cache(names, nameCache)
                nameCache.save_now()
                # Track blank puuids for post-match retroactive lookup
                pending_blank_puuids = [p for p, n in names.items() if not n.split("#")[0]]
                if pending_blank_puuids:
                    ctx.pending_post_match_lookup = (pregame_match_id, pending_blank_puuids)
                ctx.ensure_match_player_cache(pregame_match_id)
                # Pre-fetch stats for all players (concurrently; the render
                # loop below reads the warmed match cache unchanged)
                prefetch_players_parallel(
                    [_p["Subject"] for _p in Players],
                    lambda _subject: get_or_fetch_rank_and_stats(
                        _subject, pregame_match_id
                    ),
                )
                playersLoaded = 1
                with richConsole.status("Loading Players...") as status:
                    presence = presences.get_presence()
                    partyOBJ = menu.get_party_json(
                        namesClass.get_players_puuid(Players), presence
                    )
                    partyMembers = menu.get_party_members(Requests.puuid, presence)
                    partyMembersList = [a["Subject"] for a in partyMembers]
                    # Pre-warm the hidden-level lookups for exactly the
                    # players the render loop would fetch, so its in-loop
                    # calls below stay cache hits (identical bytes).
                    prefetch_players_parallel(
                        [
                            _p["Subject"]
                            for _p in Players
                            if _p["PlayerIdentity"]["HideAccountLevel"]
                            and _p["Subject"] != Requests.puuid
                            and _p["Subject"] not in partyMembersList
                        ],
                        pstats.get_level_with_fallback,
                    )
                    # log(f"retrieved names dict: {names}")
                    Players.sort(
                        key=lambda Players: Players["PlayerIdentity"].get(
                            "AccountLevel"
                        ),
                        reverse=True,
                    )
                    partyCount = 0
                    partyIcons = {}
                    names_list = []
                    for player in Players:
                        status.update(
                            f"Loading players... [{playersLoaded}/{len(Players)}]"
                        )
                        playersLoaded += 1
                        party_icon = ""

                        # set party premade icon
                        for party in partyOBJ:
                            if player["Subject"] in partyOBJ[party]:
                                if party not in partyIcons:
                                    partyIcons.update(
                                        {party: PARTYICONLIST[partyCount]}
                                    )
                                    # PARTY_ICON
                                    party_icon = PARTYICONLIST[partyCount]
                                    partyNum = partyCount + 1
                                else:
                                    # PARTY_ICON
                                    party_icon = partyIcons[party]
                                partyCount += 1
                        playerRank, previousPlayerRank, ppstats = get_or_fetch_rank_and_stats(
                            player["Subject"], pregame_match_id
                        )

                        if player["Subject"] == Requests.puuid:
                            if cfg.get_feature_flag("discord_rpc"):
                                rpc.set_data(
                                    {
                                        "rank": playerRank["rank"],
                                        "rank_name": colors.escape_ansi(
                                            NUMBERTORANKS[playerRank["rank"]]
                                        )
                                        + " | "
                                        + str(playerRank["rr"])
                                        + "rr",
                                    }
                                )

                        kd = ppstats["kd"]
                        perf_bonus = row_builder.gradient_cell(ppstats, "perf_bonus", colors.get_perf_bonus_color)

                        ranked_rating_earned = row_builder.rr_earned_cell(ppstats, colors)

                        player_level = player["PlayerIdentity"].get("AccountLevel")
                        if player["PlayerIdentity"]["Incognito"]:
                            NameColor = colors.get_color_from_team(
                                pregame_stats["Teams"][0]["TeamID"],
                                names[player["Subject"]],
                                player["Subject"],
                                Requests.puuid,
                                agent=player["CharacterID"],
                                party_members=partyMembersList,
                            )
                        else:
                            NameColor = colors.get_color_from_team(
                                pregame_stats["Teams"][0]["TeamID"],
                                names[player["Subject"]],
                                player["Subject"],
                                Requests.puuid,
                                party_members=partyMembersList,
                            )

                        if player["PlayerIdentity"]["HideAccountLevel"]:
                            if (
                                player["Subject"] == Requests.puuid
                                or player["Subject"] in partyMembersList
                            ):
                                PLcolor = colors.level_to_color(player_level)
                            else:
                                _real_level = pstats.get_level_with_fallback(player["Subject"])
                                if _real_level:
                                    PLcolor = colors.level_to_color(_real_level)
                                else:
                                    PLcolor = "?"
                        else:
                            PLcolor = colors.level_to_color(player_level)
                        if player["CharacterSelectionState"] == "locked":
                            agent_color = color(
                                agent_dict.get(player["CharacterID"].lower(), "Unknown"),
                                fore=(255, 255, 255),
                            )
                        elif player["CharacterSelectionState"] == "selected":
                            agent_color = color(
                                agent_dict.get(player["CharacterID"].lower(), "Unknown"),
                                fore=(128, 128, 128),
                            )
                        else:
                            agent_color = color(
                                agent_dict.get(player["CharacterID"].lower(), "Unknown"),
                                fore=(54, 53, 51),
                            )

                        # AGENT
                        agent = agent_color

                        # NAME
                        name = NameColor


                        # RANK
                        rankName = row_builder.rank_cell(playerRank, Ranks, cfg, cfg.table)

                        # RANK RATING
                        rr = playerRank["rr"]

                        peakRankAct = row_builder.peak_act_suffix(playerRank, cfg)
                        # PEAK RANK
                        peakRank = Ranks[playerRank["peakrank"]] + peakRankAct

                        # PREVIOUS RANK
                        previousRank = Ranks[previousPlayerRank["rank"]]

                        # LEADERBOARD
                        leaderboard = playerRank["leaderboard"]

                        hs = row_builder.gradient_cell(ppstats, "hs", colors.get_hs_gradient)
                        wr = row_builder.wr_cell(playerRank, colors)

                        if int(leaderboard) > 0:
                            is_leaderboard_needed = True

                        # LEVEL
                        level = PLcolor

                        table.add_row_table(
                            [
                                party_icon,
                                agent,
                                name,
                                "",
                                "",
                                rankName,
                                rr,
                                peakRank,
                                previousRank,
                                leaderboard,
                                hs,
                                wr,
                                kd,
                                level,
                                ranked_rating_earned,
                                perf_bonus,
                            ]
                        )
                        names_list.append(name)

                        heartbeat_data["players"][player["Subject"]] = {
                            "name": names[player["Subject"]],
                            "partyNumber": partyNum if party_icon != "" else 0,
                            "agent": agent_dict.get(player["CharacterID"].lower(), "Unknown"),
                            "rank": playerRank["rank"],
                            "peakRank": playerRank["peakrank"],
                            "peakRankAct": peakRankAct,
                            "level": player_level,
                            "rr": rr,
                            "kd": ppstats["kd"],
                            "headshotPercentage": ppstats["hs"],
                            "winPercentage": f"{playerRank['wr']} ({playerRank['numberofgames']})",
                        }

                        # bar()
            if game_state == "MENUS":
                ctx.server = ""
                already_played_with = []
                Players = menu.get_party_members(Requests.puuid, presence)
                names = namesClass.get_names_from_puuids(Players)
                _pre = sum(1 for _, _n in names.items() if not _n.split("#")[0])
                names = namesClass.resolve_aggressive(
                    names,
                    name_cache=nameCache,
                    presence_list=presence,
                    match_id=None,
                    game_state="MENUS",
                    own_puuid=Requests.puuid,
                )
                _post = sum(1 for _, _n in names.items() if not _n.split("#")[0])
                log(f"MENUS resolve: blanks {_pre} -> {_post}")
                namesClass.update_cache(names, nameCache, source="api-menus")
                nameCache.save_now()
                # Pre-warm each party member's rank/stats concurrently; the
                # spinner loop below reads the TTL cache unchanged.
                prefetch_players_parallel(
                    [_p["Subject"] for _p in Players],
                    get_menus_rank_and_stats,
                )
                playersLoaded = 1
                with richConsole.status("Loading Players...") as status:
                    Players.sort(
                        key=lambda Players: Players["PlayerIdentity"].get(
                            "AccountLevel"
                        ),
                        reverse=True,
                    )
                    seen = []
                    names_list = []
                    for player in Players:

                        if player not in seen:
                            status.update(
                                f"Loading players... [{playersLoaded}/{len(Players)}]"
                            )
                            playersLoaded += 1
                            party_icon = PARTYICONLIST[0]
                            playerRank, previousPlayerRank, ppstats = get_menus_rank_and_stats(
                                player["Subject"]
                            )
                            if player["Subject"] == Requests.puuid:
                                if cfg.get_feature_flag("discord_rpc"):
                                    rpc.set_data(
                                        {
                                            "rank": playerRank["rank"],
                                            "rank_name": colors.escape_ansi(
                                                NUMBERTORANKS[playerRank["rank"]]
                                            )
                                            + " | "
                                            + str(playerRank["rr"])
                                            + "rr",
                                        }
                                    )


                            # playerRank/previousPlayerRank/ppstats come from
                            # get_menus_rank_and_stats (TTL-cached) above.
                            kd = ppstats["kd"]
                            perf_bonus = row_builder.gradient_cell(ppstats, "perf_bonus", colors.get_perf_bonus_color)

                            ranked_rating_earned = row_builder.rr_earned_cell(ppstats, colors)

                            player_level = player["PlayerIdentity"].get("AccountLevel")
                            PLcolor = colors.level_to_color(player_level)

                            # AGENT
                            agent = ""

                            # NAME
                            name = color(names[player["Subject"]], fore=(76, 151, 237))

                            # RANK
                            rankName = row_builder.rank_cell(playerRank, Ranks, cfg, cfg.table)

                            # RANK RATING
                            rr = playerRank["rr"]

                            peakRankAct = row_builder.peak_act_suffix(playerRank, cfg)

                            # PEAK RANK
                            peakRank = Ranks[playerRank["peakrank"]] + row_builder.peak_act_suffix(playerRank, cfg)

                            # PREVIOUS RANK
                            previousRank = Ranks[previousPlayerRank["rank"]]

                            # LEADERBOARD
                            leaderboard = playerRank["leaderboard"]

                            hs = row_builder.gradient_cell(ppstats, "hs", colors.get_hs_gradient)
                            wr = row_builder.wr_cell(playerRank, colors)

                            if int(leaderboard) > 0:
                                is_leaderboard_needed = True

                            # LEVEL
                            level = PLcolor

                            table.add_row_table(
                                [
                                    party_icon,
                                    agent,
                                    name,
                                    "",
                                    "",
                                    rankName,
                                    rr,
                                    peakRank,
                                    previousRank,
                                    leaderboard,
                                    hs,
                                    wr,
                                    kd,
                                    level,
                                    ranked_rating_earned,
                                    perf_bonus,
                                ]
                            )
                            names_list.append(name)

                            heartbeat_data["players"][player["Subject"]] = {
                                "name": names[player["Subject"]],
                                "rank": playerRank["rank"],
                                "peakRank": playerRank["peakrank"],
                                "peakRankAct": peakRankAct,
                                "level": player_level,
                                "rr": rr,
                                "kd": ppstats["kd"],
                                "headshotPercentage": ppstats["hs"],
                                "winPercentage": f"{playerRank['wr']} ({playerRank['numberofgames']})",
                            }

                            # bar()
                    seen.append(player["Subject"])
            if (title := game_state_dict.get(game_state)) is None:
                # program_exit(1)
                time.sleep(9)
            
            title_parts = [f"VALORANT status: {title}"]
            
            if game_state == "PREGAME" and pregame_stats is not None and cfg.get_feature_flag("starting_side"):
                ctx.team_side = "Attacker" if pregame_stats["AllyTeam"]["TeamID"] == "Red" else "Defender"
                title_parts.append(f" | {colr(ctx.team_side, fore=(76, 151, 237) if ctx.team_side == 'Defender' else (238, 77, 77))}")
            
            if cfg.get_feature_flag("server_id") and ctx.server != "":
                parts = ctx.server.split('.')
                region = parts[2] if len(parts) > 2 else ctx.server
                location = GAMEPOD_REGION_MAP.get(region, region)
                title_parts.append(f" | {colr(location, fore=(200, 200, 200))}")
            
            table.set_title(''.join(title_parts))
            
            if title is not None:
                if cfg.get_feature_flag("auto_hide_leaderboard") and (
                    not is_leaderboard_needed
                ):
                    table.set_runtime_col_flag("Pos.", False)

                if game_state == "MENUS":
                    table.set_runtime_col_flag("Party", False)
                    table.set_runtime_col_flag("Agent", False)
                    table.set_runtime_col_flag(cfg.weapon.capitalize(), False)
                    table.set_runtime_col_flag("Phantom", False)

                if game_state == "INGAME":
                    if isRange:
                        table.set_runtime_col_flag("Party", False)
                        table.set_runtime_col_flag("Agent", False)

                # We don't to show the RR column if the "aggregate_rank_rr" feature flag is True.
                table.set_runtime_col_flag(
                    "RR",
                    cfg.get_table_flag("rr")
                    and not cfg.get_feature_flag("aggregate_rank_rr"),
                )

                # Render-on-change: skip the draw when the fingerprint of
                # what would be printed matches the last drawn output. The
                # heartbeat still goes out so the phone companion stays live.
                _sig_players = []
                for _sp, _sd in heartbeat_data.get("players", {}).items():
                    _sig_players.append({
                        "puuid": _sp,
                        "agent": str(_sd.get("agent", "")),
                        "team": str(_sd.get("partyNumber", "")),
                        "rank": str(_sd.get("rank", "")),
                        "rr": str(_sd.get("rr", "")),
                        "level": str(_sd.get("level", "")),
                    })
                _sig_extra = ""
                _pregame_pending = False
                _pregame_enemy_count = 0
                snapshot = {
                    "puuids": [],
                    "agents": {},
                    "v3_names": {},
                    "loadouts_status": "idle",
                    "v3_status": "",
                }
                if game_state == "PREGAME" and pregame_match_id:
                    # One merged read of both probes per iteration; every
                    # downstream decision (sig, settle gate, titles, rows)
                    # consumes this single snapshot.
                    try:
                        snapshot = enemy_snapshot(
                            pregame_match_id,
                            cfg.get_feature_flag("enemy_loadouts_probe") and pregame_match_id is not None,
                            cfg.get_feature_flag("enemy_probe_v3") and pregame_match_id is not None,
                            log,
                        )
                    except Exception as _snap_exc:
                        log(f"[enemy-table] probe snapshot exc: {type(_snap_exc).__name__}: {_snap_exc}")
                if game_state == "PREGAME" and pregame_match_id:
                    _ally_in_hb = set(heartbeat_data.get("players", {}).keys())
                    for _ep in snapshot["puuids"]:
                        if _ep in _ally_in_hb:
                            continue
                        _pregame_enemy_count += 1
                        _sig_players.append({
                            "puuid": _ep,
                            "agent": str(snapshot["agents"].get(_ep, "")),
                            "team": "enemy",
                        })
                    _prev_names = ctx.last_enemy_names.get(pregame_match_id, {})
                    if _prev_names:
                        _sig_extra = "names:" + ";".join(
                            sorted(f"{k}={v}" for k, v in _prev_names.items()))
                        _pregame_pending = any(not v for v in _prev_names.values())
                _signature = render_signature(game_state, _sig_players)
                # STRICT single print per agent select: once drawn, stay
                # silent for the rest of PREGAME; before the first PREGAME
                # draw, hold only while the enemy table may still improve.
                if game_state == "PREGAME" and pregame_match_id:
                    if ctx.last_draw_state == "PREGAME":
                        Server.send_payload("heartbeat", heartbeat_data)
                        continue
                    _pg_probe = (
                        snapshot["loadouts_status"]
                        if cfg.get_feature_flag("enemy_loadouts_probe")
                        else "complete"
                    )
                    if pregame_draw_decision(
                        time.time() - ctx.state_entered_at,
                        _pregame_enemy_count,
                        _pg_probe,
                    ) == "wait":
                        Server.send_payload("heartbeat", heartbeat_data)
                        continue
                if _sig_extra:
                    _signature += "|" + _sig_extra
                # No timed force refresh of any kind: a mid-match table never
                # changes, so a timer can only manufacture identical prints.
                _redraw_force = float("inf")
                if ctx.last_drawn_signature is None:
                    _draw_reason = "first"
                elif _signature != ctx.last_drawn_signature:
                    _draw_reason = "changed"
                else:
                    _draw_reason = "force"
                # Pending-forcing bound: while an enemy name is still
                # unresolved, redrawing is allowed only until the enemy count
                # is stable since the last drawn enemy count AND at most 30
                # seconds since the last draw, then stop forcing; a
                # never-resolving name therefore cannot force identical
                # redraws forever.
                _pending_force = (
                    _pregame_pending
                    and _pregame_enemy_count != ctx.last_drawn_enemy_count
                    and (time.time() - ctx.last_draw_time) < 30.0
                )
                if (not should_redraw(_signature, ctx.last_drawn_signature,
                                      time.time() - ctx.last_draw_time, _redraw_force)
                        and game_state == ctx.last_draw_state
                        and not _pending_force):
                    Server.send_payload("heartbeat", heartbeat_data)
                    continue
                log(f"redraw: reason={_draw_reason} state={game_state}")
                ctx.last_drawn_signature = _signature
                ctx.last_draw_state = game_state
                ctx.last_draw_time = time.time()
                ctx.last_drawn_enemy_count = _pregame_enemy_count

                table.set_caption(f"VRY ShowNames v{version}")
                Server.send_payload("heartbeat",heartbeat_data)
                table.display()
                firstPrint = False

                enemy_names = {}
                if game_state == "PREGAME" and cfg.get_feature_flag("enemy_table_preview"):
                    confirmed_enemies = snapshot["v3_names"]
                    v3_status = snapshot["v3_status"]
                    loadouts_on = cfg.get_feature_flag("enemy_loadouts_probe") and pregame_match_id is not None
                    loadouts_status = snapshot["loadouts_status"] if loadouts_on else "idle"
                    enemy_puuids = list(snapshot["puuids"]) if loadouts_on else []
                    built_rows = []
                    if enemy_puuids:
                        enemy_agents = snapshot["agents"] if loadouts_on else {}
                        try:
                            enemy_names = resolve_enemy_names(
                                pregame_match_id, enemy_puuids, namesClass, nameCache,
                                presence, Requests.puuid, log
                            )
                        except Exception as _ene:
                            log(f"[enemy-table] enemy name resolve exc: {type(_ene).__name__}: {_ene}")
                            enemy_names = {}
                        if enemy_names:
                            # Mirror for the render signature: when a blank name
                            # later resolves, the pending-blank gate forces a
                            # redraw so the table never stays stale.
                            ctx.last_enemy_names[pregame_match_id] = dict(enemy_names)
                        try:
                            built_rows = build_enemy_rows(
                                enemy_puuids, enemy_agents, enemy_names,
                                lambda _ep: get_or_fetch_rank_and_stats(_ep, pregame_match_id),
                                cfg, Ranks, NUMBERTORANKS, colors, cfg.table, agent_dict,
                                level_fn=lambda _ep: enemy_level_for(pregame_match_id, _ep)
                            )
                        except Exception as _ebr:
                            log(f"[enemy-table] enemy row build exc: {type(_ebr).__name__}: {_ebr}")
                            built_rows = []
                    enemy_table = Table(cfg, log)
                    if built_rows:
                        enemy_title = (
                            f"[green]Enemy Team[/green] "
                            f"[dim]({len(built_rows[:5])}/5 revealed via loadouts)[/dim]"
                        )
                        if loadouts_status.startswith("error"):
                            enemy_title = (
                                f"[green]Enemy Team[/green] "
                                f"[dim]({len(built_rows[:5])}/5 revealed via loadouts | {loadouts_status})[/dim]"
                            )
                        enemy_table.set_title(enemy_title)
                    elif confirmed_enemies:
                        enemy_table.set_title(
                            f"[green]Enemy Team[/green] "
                            f"[dim]({len(confirmed_enemies)}/5 revealed via v3)[/dim]"
                        )
                    elif loadouts_on and loadouts_status.startswith("error"):
                        enemy_table.set_title(
                            f"[red]Enemy Team[/red] "
                            f"[dim]({loadouts_status})[/dim]"
                        )
                    elif loadouts_on:
                        enemy_table.set_title(
                            "[red]Enemy Team[/red] "
                            "[dim](probing enemy loadouts...)[/dim]"
                        )
                    elif v3_status == "running":
                        enemy_table.set_title(
                            "[red]Enemy Team[/red] "
                            "[dim](v3 probing... hidden during agent select)[/dim]"
                        )
                    else:
                        enemy_table.set_title(
                            "[red]Enemy Team[/red] "
                            "[dim](hidden by Riot during agent select)[/dim]"
                        )
                    for row in built_rows[:5]:
                        enemy_table.add_row_table(row)
                    revealed_rows = [] if built_rows else list(confirmed_enemies.items())
                    for puuid, gametag in revealed_rows[:5]:
                        enemy_table.add_row_table([
                            "",
                            "[dim]?[/dim]",
                            f"[bold yellow]{gametag}[/bold yellow]",
                            "",
                            "",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                        ])
                    for _ in range(max(0, 5 - len(built_rows[:5]) - len(revealed_rows[:5]))):
                        enemy_table.add_row_table([
                            "",
                            "[dim]?[/dim]",
                            "[dim]Hidden[/dim]",
                            "",
                            "",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                            "[dim]?[/dim]",
                        ])
                    if cfg.get_feature_flag("auto_hide_leaderboard") and not is_leaderboard_needed:
                        enemy_table.set_runtime_col_flag("Pos.", False)
                    enemy_table.set_runtime_col_flag(
                        "RR",
                        cfg.get_table_flag("rr") and not cfg.get_feature_flag("aggregate_rank_rr"),
                    )
                    enemy_table.display()

                print("\nNames:")
                for n in names_list:
                    print(n)
                enemy_name_values = [v for v in enemy_names.values() if v]
                if enemy_name_values:
                    print("\nEnemy names:")
                    for n in enemy_name_values:
                        print(colr(n, fore=(255, 255, 0)))

                # print(f"VRY ShowNames v{version}")
                if cfg.get_feature_flag("last_played"):
                    if len(already_played_with) > 0:
                        print("\n")
                        for played in already_played_with:
                            print(
                                f"Already played with {played['name']} (last {played['agent']}) {stats.convert_time(played['time_diff'])} ago. (Total played {played['times']} times)"
                            )
                already_played_with = []
        if cfg.cooldown == 0:
            input("Press enter to fetch again...")
        else:
            # time.sleep(cfg.cooldown)
            pass
except KeyboardInterrupt:
    # lame implementation of fast ctrl+c exit
    os._exit(0)
except:
    log(traceback.format_exc())
    fatal_banner = (
        "The program has encountered an error. If the problem persists, please reach support"
        f" with the logs found in {os.getcwd()}\\logs"
    )
    log(f"FATAL: unhandled exception; banner shown to user: {fatal_banner}")
    print(color(fatal_banner, fore=(255, 0, 0)))
    input("press enter to exit...\n")
    os._exit(1)
