# Changelog

## 2026-09-13

### Added

- Single-instance guard. A second copy of the app now exits immediately with a friendly message instead of fighting the first copy over the console and log files. The lock self-heals from crashed runs.

- Game mode text override. New `rpc_overrides` key: `game_mode` (`real` or any
  free-text mode name, e.g. `Unrated`, `Premier`) replaces the queue-derived
  mode text in all three Discord card states (" Lobby - <mode>",
  "Agent Select - <mode>", and "<mode> // <score>" in match). It is free text
  by design (no fixed mode list to validate against); an empty value warns
  and falls back to real. Asked in the configurator's Discord Appearance page.

### Added

- Discord card pin and map override. New `rpc_overrides` keys: `rpc_state`
  (`real`/`lobby`/`agent_select`/`ingame`) pins the card to one look regardless
  of the real game state (timer keeps counting; real transitions are suppressed
  while pinned), and `map` (`real` or a map display name, e.g. `Ascent`)
  replaces the big image on the agent-select and in-match cards. Both default
  to `real`; invalid values produce a named warning and fall back to real. The
  two entries are asked in the configurator's Discord Appearance page.

## 2026-09-12

### Added

- Enemy team reveal during agent select. Names, ranks, RR, peak rank, headshot
  percentage, win rate, KD and locked agents are read from the pregame loadouts
  list and fill into a dedicated "Enemy Team" table progressively as data
  becomes available. Feature flag `enemy_loadouts_probe` (on by default); the
  previous core-game race remains as a fallback.
- Enemy level column. Levels come from each player's most recent accessible
  match. If Riot no longer keeps that match's details, the tool walks the
  player's recent history until it finds one that still has a record, and "?"
  is shown only when no record exists at all.
- "Enemy names:" section printed below the team names list.
- Real account levels are shown for players who hide them (teammates and
  enemies, during agent select and in-game). The game's 0 placeholder is
  replaced by the level from match history, or "?" when nothing is on record.
  You and your party keep the exact current level, which the game reports
  truthfully for you.
- Adaptive state polling. The app checks the game state every 2 seconds during
  agent select and matches and uses the configured cooldown otherwise. Up to 3
  consecutive failed checks are tolerated before any disconnect handling; a
  single failed check keeps the previous state.
- Discord appearance overrides (new `rpc_overrides` config section, editable in
  `config.json` or the configurator): choose any rank for the Discord card badge
  (all three states), any agent name in the in-match details line, and any party
  size / party max / score numbers (party in all states, scores in-match only).
  Every value defaults to "real": the card looks exactly as before until you set
  an override. Invalid values are logged and fall back to "real".
- Regression test suites: `tests/test_enemy_reveal.py`,
  `tests/test_level_fallback.py`, `tests/test_static_content.py`,
  `tests/test_skin_seam.py`, `tests/test_ws_stop.py`,
  `tests/test_state_polling.py`.

### Changed

- Settings schema declared in `src/settings.py` and validated at load: unknown
  keys and wrong-typed values are logged by name (values are kept as before);
  the wildcard `from src.constants import *` in `main.py` was replaced with an
  explicit import list. No behavior change.
- Duplicated table-cell formatting (rank, peak rank, gradients, win rate,
  earned RR, levels) unified into `src/row_builder.py`; the printed tables
  are byte-identical (no visible change).

- Internal state consolidated into a `MatchContext` object
  (`src/match_context.py`); no behavior change.

- Faster startup. The name-cache warm-up runs in a background thread and is
  parallelized, so the first screen appears without waiting for the 25-match
  history walk (about 3 seconds instead of about 10 on the reference machine).
- Static game-content databases (weapons, skins, sprays, buddies, agents,
  titles, player cards) are fetched once per session and cached in-process
  (`src/static_content.py`), removing several repeated downloads per refresh
  cycle.
- Print on change. A new block is printed only when what would be printed
  actually changes (state, party, ranks, stats, enemy reveal progress), so
  identical blocks no longer repeat. Output scrolls naturally like the
  classic app: new blocks print below older ones and the screen is never
  wiped. Each actual print logs a `redraw: reason=...` line. Menus and
  agent select print on change only; matches add the timer-based refresh
  for slow-moving data.
- Party rank and stat lookups in the menus are cached for 60 seconds.
- Name-service lookups skip players already resolved this session.
- Per-cycle logging waste removed: the log filename is computed once per run
  and table rows are no longer dumped to the log on every refresh.
- Dead code removed (about 54 lines): unused imports, functions and constants
  with no references. Several "dead" candidates were re-checked and kept
  because they turned out to be in use.
- `TROUBLESHOOTING.md` updated (new sections 8 to 10), including a postmortem
  of a reconnect-storm experiment that was reverted to the proven per-cycle
  connection.

### Fixed

- Long-standing crash at match boundaries. When the game momentarily reports
  an empty private presence, the app no longer crashes with `TypeError:
  argument of type 'NoneType' is not iterable` in the in-game branch. This
  crash existed in the original code (visible in old session logs and
  reproduced in the 2026-09-12 session).

### Rollback and folder conventions

- Dated full-source snapshots are kept under `backups/<date>-<label>/`
  (for example `backups/2026-09-12-pre-optimization/`). Restoring a phase
  means copying its files back from the snapshot.
- The maintainer also keeps a separate, untouched source copy outside this
  repository as the stable fallback; the experimental directory is where all
  modifications happen.
