# Troubleshooting

This document matches the current codebase. The fix that resolves anonymized players (`#` rendered names) is the multi-region name-service workaround.

## 1. Where logs live

Path: `logs/log-N.txt` in the project directory. The newest log has the highest N. Each launch creates a fresh file.

## 2. Resolver order (per render — runs in INGAME, PREGAME, and MENUS states)

1. Persistent name cache lookup (instant).
2. `match-details-current` — query Riot's match-details for the active match.
3. `full-probe` — invokes the multi-region name-service against every shard (NA, EU, KR, LATAM, BR, AP, PBE) except your home shard.

The first method to resolve a puuid wins.

In addition, **at startup** Phase 2 walks your last 25 matches via `match-history` + `match-details` to pre-warm `name_cache.json` with every co-player's name.

## 3. Log prefixes you might see

| Prefix | Meaning | Healthy outcome |
|---|---|---|
| `bootstrap: N new names from M matches` | Phase 2 startup pre-warm | N>0 means cache populated; N=0 means no recent match history (new account) |
| `resolve_aggressive: starting with N blank puuids` | A render is starting and saw N anonymized players | Followed by per-method results below |
| `[multi-region] shard=X -> HTTP 200` | Cross-shard query succeeded | Each non-home shard should return 200 |
| `[multi-region] HIT shard=X <puuid8> = <name>#<tag>` | Resolution succeeded for a hidden player | This is the win condition |
| `[multi-region] resolved K/N` | K out of N puuids recovered | K=N is best; K<N means some accounts aren't on those shards |
| `[full-probe] resolved K/N` | Wrapper summary | Same as multi-region in practice |
| `resolve_aggressive[full-probe]: filled K, remaining=M` | After full-probe, N-M still blank | M=0 is best |
| `INGAME resolve: blanks N -> M` | Per-state delta | M < N means the chain helped |
| `resolve_aggressive: GIVE UP on N puuids: [...]` | Nothing worked for those puuids | List the puuids in your bug report |
| `Retroactive resolved N names from <match_id>` | Post-match harvest filled the cache | Helps next match |

## 4. Concrete grep recipes (PowerShell)

Newest log:
```powershell
$log = Get-ChildItem .\logs\log-*.txt | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

All resolver outcomes:
```powershell
Get-Content $log | Select-String -Pattern '\[multi-region\]|\[full-probe\]|bootstrap:|resolve_aggressive|INGAME resolve|PREGAME resolve|MENUS resolve|Retroactive resolved'
```

Multi-region hits only:
```powershell
Get-Content $log | Select-String -Pattern '\[multi-region\] HIT'
```

Per-render delta:
```powershell
Get-Content $log | Select-String -Pattern '(INGAME|PREGAME|MENUS) resolve: blanks'
```

Give-ups:
```powershell
Get-Content $log | Select-String -Pattern 'GIVE UP'
```

## 5. Decision tree

```
1. Did `INGAME resolve: blanks N -> 0` appear?
   YES -> resolver works, you're done.
   NO  -> continue.

2. Did `[multi-region] HIT shard=...` appear at least once?
   YES -> partial success; some puuids still missing — see step 3.
   NO  -> all multi-region shards either 401/403/404 or returned blank.
          Check that VALORANT is running and you're authed; restart Riot Client.

3. Are there `resolve_aggressive: GIVE UP on N puuids` lines?
   YES -> those puuids aren't on any of NA/EU/KR/LATAM/BR/PBE.
          Likely brand-new accounts or accounts on a shard not queried.
          Note the puuid prefixes from the GIVE UP line; share for diagnosis.
   NO  -> congrats, all puuids are resolved.

4. Did `bootstrap: N new names from M matches` show N>0?
   YES -> startup pre-warm filled the cache.
   NO  -> empty match history. Play a competitive game and relaunch.
```

## 6. What to share if nothing works

- Reproduce the `#` issue in a real match.
- Locate the latest `logs/log-N.txt`.
- Note the puuids of the affected players from any `GIVE UP on` line (or from the table itself by inspection).
- Report Valorant patch / region / time of day plus the log file.

## 7. Enemy reveal during agent select

During agent select (PREGAME) the tool reveals the enemy team through two read-only sources, in order:

1. **Loadouts probe** (flag `enemy_loadouts_probe`, default on). The game client itself fetches the pregame loadouts list (weapon skins) during agent select, and that list contains every player in the lobby. The tool polls the same list, subtracts your own team, and the remaining puuids are the enemies. Names, locked agents, ranks, stats and account levels are then fetched per player. Enemies typically appear as they lock in their agents, so the enemy table fills up progressively.
2. **Core-game race** (flag `enemy_probe_v3`). If the loadouts list stays empty, the tool keeps polling the in-game match endpoint; the moment the match server spins up (usually seconds before the loading screen) it returns all ten players and the table fills there. This fallback also fills in anything the loadouts probe missed.

The enemy table itself is rendered when the flag `enemy_table_preview` is on. All three flags are togglable in the configurator ("Optional Feature Flags") and default to on for the reveal features.

Log prefixes for the loadouts probe:

| Prefix | Meaning |
|---|---|
| `loadouts probe: starting for match=...` | Polling started for the current agent select |
| `loadouts probe: N subjects, M enemies so far` | Progress; M climbs to 5 as enemies lock agents |
| `loadouts probe: complete, 5 enemies` | All five enemies identified |
| `loadouts probe: no enemy subjects before budget end` | The list never contained opponents this match (see section 8) |

Grep for the whole flow:
```powershell
$log = Get-ChildItem .\logs\log-*.txt | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $log | Select-String -Pattern 'loadouts probe|\[v3-race\]|\[v3-verify\]'
```

## 8. Enemy table stays empty during agent select

Work through these checks in order:

1. **Is the feature on?** Check `config.json` for `"enemy_loadouts_probe": true` and `"enemy_table_preview": true` under `flags` (missing keys default to on, but an explicit `false` disables). Also check the configurator checkboxes.
2. **What does the probe log say?** Run the grep from section 7.
   - `N subjects, M enemies so far` with M climbing: everything works; the table fills as enemies lock.
   - `N subjects` stuck at 5 (only your team) for the whole agent select: your region or queue is not returning opponents in the loadouts list. This endpoint was historically restricted in some regions (the old skin-list code in this repo was disabled for exactly that reason). The core-game race fallback takes over seconds before loading.
   - No `loadouts probe` lines at all: the probe did not start. Confirm the match actually reached agent select (look for `new game state: PREGAME`) and that the flag is on.
3. **Did the fallback fire?** Look for `[v3-race] *** COREGAME ALIVE ***` and `[v3-race] *** EARLY ENEMY REVEAL *** N confirmed real`. If those appear, the enemy table fills at the loading screen even if the loadouts list stayed empty.
4. **Still nothing?** Share the latest `logs/log-N.txt` plus your region and queue (competitive/TDM/etc.), noting whether any `loadouts probe:` or `[v3-race]` lines appeared.

The reveal is read-only (same local API the game client uses, no game files touched) and is a Riot-side data exposure that could be restricted or patched at any time; if that happens the enemy table will simply stay hidden again, with the loadouts probe lines in the log as evidence.

## 9. Level shows 0 or ?

The game reports 0 for any player who hides their account level. The tool replaces that 0 with the player's real level, read from their most recent accessible match: it tries the newest match first and, if Riot no longer keeps its details, walks the player's recent matches (any queue) until one still has a record. The result is cached per player, so this costs at most a few requests once per player. This applies to teammates and enemies alike, during agent select and in game. You and your party always show the exact current level, since the game reports it truthfully for you.

Two things to expect:

- A recovered level can lag one or two levels behind if the player leveled up very recently (it reflects their level as of their last recorded match).
- If a player has no accessible match record at all (very old or inactive account), the cell shows `?` instead of a misleading 0.

Log lines, written at most once per player:

| Prefix | Meaning |
|---|---|
| `level fallback: <puuid8> via older match` | The newest match was gone; an older match provided the level |
| `level unresolved: <puuid8> (no accessible match details)` | Nothing was found; the cell shows `?` |

Grep for both:
```powershell
Get-Content $log | Select-String -Pattern 'level fallback|level unresolved'
```

If a level stays `?`, those lines tell you why; share them in any bug report.

## 10. Reconnect loop / disconnected-reconnect spam (postmortem 2026-09-12)

**What you would have seen.** The console cycled every few seconds: a yellow `Disconnected from Valorant. Attempting to reconnect...`, then a green `Reconnected successfully! Loading...`, then the tables redrawing, then the same two messages again. The app never settled.

**What actually happened.** During a speed experiment the app kept one open connection to the game's event feed instead of opening a fresh one each refresh. That experimental listener dropped and reconnected roughly every three seconds. Each reconnect collided with the app's own per-cycle "am I still connected?" check, and a single failed check was enough to trigger the full disconnect routine: reread the lockfile, refresh the auth token, restart the listener. The restart dropped again, which looked like another disconnect, which ran the routine again. A loop with no way out, caused by the timing of two internal steps, not by the game, the account, or anything server side.

**How it was fixed.** The experimental pacing was reverted to the original, proven approach: open a connection per refresh and close it after. Everything else from the speed work was kept (faster startup, cached databases, leaner logging, party stats cache), and a safeguard now exists so a replacement listener can never be started while an old one is still alive.

**Log signature, if you ever suspect it again:**

| Signature | Meaning |
|---|---|
| `first game state: MENUS` (or any state) repeating every 2-3 seconds | The app is re-entering its startup check over and over, the hallmark of the loop |
| `opened lockfile` / `https://127.0.0.1:<port>/entitlements/v1/token` / `got version from logs` repeating in the same rhythm | A listener is reconnecting on every cycle |
| `Connection error on local request. Retrying... (N/3)` | The game client stopped answering (typically because it was closed) |
| `Failed to connect to local client after 3 attempts.` | The final local failure before the tool exits |

Grep for the whole pattern:
```powershell
Get-Content $log | Select-String -Pattern 'first game state|opened lockfile|entitlements|got version|Connection error'
```

**Healthy behaviour to compare against:** `first game state` appears exactly once per launch, `new game state` lines appear only when the state actually changes, and the lockfile/entitlements/version lines appear once at startup rather than every few seconds.

## 11. How state detection works now (adaptive tolerant polling)

The app notices what the game is doing (menus, agent select, in match) by checking a small status feed on your own machine. It checks every 2 seconds during agent select and matches, and at your normal cooldown speed otherwise, so state changes appear quickly without any extra background connections.

A single failed check is ignored: the app keeps its previous view and tries again. Only after three failed checks in a row does it run the disconnect routine, so a momentary hiccup never causes a full reconnect. If you enable the chat display option, the older websocket path is kept exactly as it was, with the same three-check tolerance added.

When diagnosing state problems, look for the `new game state` lines: with healthy checks they appear only when the state truly changes, and the app keeps reporting the previous state through short hiccups instead of dropping to disconnected.

## 12. How rendering works now (print on change, classic scrolling)

The app prints a new block only when what would be printed actually changes. Before each print it compares a small fingerprint of the current view (game state, party members, ranks, agents, enemy reveal progress) against the last printed one. If they are identical, nothing is printed: identical blocks no longer repeat.

The console always scrolls naturally, exactly like the classic app: new blocks print below older ones and the screen is never wiped or cleared.

A new block prints only when:

- the fingerprint changes: someone joins or leaves, ranks or stats update, an enemy is revealed or locks an agent, or the game state changes.

No state has a refresh timer. Every draw is data-driven: the log reason is either "first" (the first draw after start-up) or "changed" (something actually changed). Agent select prints exactly once: the first print is held until the enemy table settles (all five enemies found, or the probe finishes or gives up, or at most 20 seconds), then nothing prints again for the rest of agent select; teammate and enemy agent locks never print on their own. During a match, the table prints once and stays; mid-match data does not change, so it never reprints.

Each actual print writes one `redraw: reason=...` line to the log (`first`, `changed`, `forced`, `state-changed`), so print frequency is verifiable. If blocks still stack, check whether the `redraw:` lines appear in bursts; repeated `reason=forced` every cycle would mean the fingerprint is unstable and should be investigated, while no `redraw:` lines with a stale screen mean the gate is skipping a draw it should make.
