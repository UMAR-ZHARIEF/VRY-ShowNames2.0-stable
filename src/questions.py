from InquirerPy.base.control import Choice
from src.constants import DEFAULT_CONFIG, WEAPONS

TABLE_OPTS = {
    "skin": "Skin",
    "rr": "Ranked Rating",
    "earned_rr": "RR gained or lost (and AFK Penalties)",
    "leaderboard": "Leaderboard Position",
    "peakrank": "Peak Rank",
    "previousrank": "Previous Act Rank",
    "headshot_percent": "Headshot Percentage",
    "winrate": "WinRate",
    "kd": "K/D Ratio <!> Last Game Only <!>",
    "level": "Account Level"
}

FLAGS_OPTS = {
	"last_played": "Last Played Stats",
	"auto_hide_leaderboard": "Auto Hide Leaderboard Column",
    "pre_cls": "Pre-Clear Screen",
    "peak_rank_act": "Peak Rank Act",
    "discord_rpc": "Discord Rich Presence",
    "aggregate_rank_rr": "Display Rank and Ranked Rating in the same column",
    "server_id": "Show Server Region ID",
    "short_ranks": "Short rank names instead of long ones",
    "truncate_skins": "Truncate long skin names if the window is too small",
    "truncate_names": "Truncate long player names if the window is too small",
	"starting_side": "Display starting side (Attacker/Defender) while in the pregame lobby",
    "enemy_table_preview": "Show enemy team table during agent select",
    "enemy_probe_v3": "Enemy reveal fallback probe (core-game race, v3)",
    "enemy_loadouts_probe": "Reveal enemy names/ranks during agent select (loadouts probe)"
}

RPC_OVERRIDE_OPTS = {
    "rank": "Rank shown on the Discord card badge (e.g. 'Immortal 1', or 'real' for your actual rank)",
    "agent": "Agent shown in the in-match details text (e.g. 'Sova', or 'real' for your locked agent)",
    "party_size": "Party size shown on the card (a number, or 'real')",
    "party_max": "Party max size shown on the card (a number, or 'real')",
    "score_ally": "Your team's score shown in-match (a number, or 'real')",
    "score_enemy": "Enemy team's score shown in-match (a number, or 'real')",
    "rpc_state": "Which Discord card is pinned: 'real', 'lobby', 'agent_select' or 'ingame'",
    "map": "Map shown on the agent-select and in-match cards (e.g. 'Ascent', or 'real')",
    "game_mode": "Game mode text shown on the card (e.g. 'Unrated', 'Premier', or 'real' for your real queue)",
    "game_mode": "Game mode shown on the card (any text, or 'real')"
}

rpc_overrides_questions = lambda config: [
    {
        "type": "input",
        "name": key,
        "message": text,
        "default": config.get("rpc_overrides", DEFAULT_CONFIG["rpc_overrides"]).get(
            key, DEFAULT_CONFIG["rpc_overrides"][key]
        ),
    }
    for key, text in RPC_OVERRIDE_OPTS.items()
]

weapon_question = lambda config: {
        "type": "fuzzy",
        "name": "weapon",
        "message": "Please select a weapon to show skin for:",
        "default": config.get("weapon","Vandal"),
        "choices": WEAPONS,
    }

table_question = lambda config: {
        "type": "checkbox",
        "name": "table",
        "message": "Please select table columns to display:",
        "choices": [
            Choice(k, name=v, enabled=config.get("table",DEFAULT_CONFIG["table"]).get(k, DEFAULT_CONFIG["table"][k]))
            for k, v in TABLE_OPTS.items()
        ],
        "filter": lambda table: {k: k in table for k in TABLE_OPTS.keys()},
        "long_instruction": "Press 'space' to toggle selection and 'enter' to submit"
    }

port_question = lambda config: {
        "type": "number",
        "name": "port",
        "message": "Please enter port for server to run:",
        "default": config.get("port", 1100),
        "min_allowed":0,
        "max_allowed": 65535,
        "filter": lambda ans: int(ans)
    }

flags_question = lambda config: {
        "type": "checkbox",
        "name": "flags",
        "message": "Please select optional features:",
        "choices": [
            Choice(k, name=v, enabled=config.get("flags",DEFAULT_CONFIG["flags"]).get(k, DEFAULT_CONFIG["flags"][k]))
            for k, v in FLAGS_OPTS.items()
        ],
        "filter": lambda flags: {k: k in flags for k in FLAGS_OPTS.keys()},
        "long_instruction": "Press 'space' to toggle selection and 'enter' to submit"
    }

basic_questions = lambda config: [
    weapon_question(config=config),
    table_question(config=config)
]

advance_questions = lambda config: [
    port_question(config=config),
] + basic_questions(config=config)
