<h3 align="center"> VRY ShowNames</h3>

Fork of [VALORANT rank yoinker](https://github.com/zayKenyon/VALORANT-rank-yoinker)

- Shows hidden names  
- Removes update system  

<br/>

## How it works

The `#`-rendered names you see in some Valorant lobbies are players who enabled "Anonymize My Riot ID" in their Riot account settings. Riot's name-service redacts these names on your home shard, but **not on other regional shards** (NA, EU, KR, LATAM, BR, PBE). VRY-ShowNames uses a multi-region fallback strategy: it queries the same name-service on each non-home shard until one returns the real Riot ID, then caches the result in `name_cache.json` for instant lookup on future encounters. At startup, the tool also pre-warms the cache by walking your last 25 matches and harvesting every co-player's name from `match-details` (which historically retains real names even for accounts that later anonymized). Both mechanisms are read-only API calls — nothing inside Valorant or Vanguard is modified.

During agent select, the game client itself fetches a loadouts list (the same data used to show weapon skins) that contains an entry for every player in the lobby, including the enemy team. VRY-ShowNames reads that list, subtracts your own team, and reveals the remaining five players progressively: name, locked agent, rank, RR, peak rank and recent stats, shown in the enemy table while you are still picking agents. If the list stays empty (some regions have historically restricted it), the tool falls back to its core-game race, which reveals the same data the moment the match server spins up. Everything stays read-only: no game files are modified.

Account levels get the same treatment. The game reports 0 for players who hide their account level; VRY-ShowNames replaces that with the player's real level, read from their most recent accessible match, for teammates and enemies alike, during agent select and in game. If no accessible match record exists at all, the cell shows `?` instead. You and your party always keep the exact current level, since the game reports it truthfully for you.

For technical details, see [CLEANUP_PLAN.md](CLEANUP_PLAN.md) for the current code state, [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for runtime diagnostics, and [BACKUP.md](BACKUP.md) for the pre-cleanup snapshot.

<br/>

## Running:

1) Download Python [3.11](https://www.python.org/downloads/release/python-3119/) or [3.10](https://www.python.org/downloads/release/python-31011/), make sure it is added to the PATH. (This is an option on installation.)
2) Download the [source](https://github.com/pintoso/VRY-ShowNames/archive/refs/heads/master.zip).
3) Run `INSTALL.bat`
4) Run `START.bat`

<br/>

#### (Optional) Shortcut
1. Right-click on `START.bat`.
2. Create a shortcut.
   - (Optional) Change the icon:
     - Right-click on the shortcut.
     - Select `Properties` > `Change Icon`.

##

<br/>

#### "Why not provide a public .exe?" [Read here](https://github.com/pintoso/VRY-ShowNames/issues/6#issuecomment-3391663074)
##

<br/>
<br/>

#### (Optional) Compiling:

1) `pip install cx_Freeze`
2) `python setup.py build`
3)  Open the new Build folder and find `VRY ShowNames.exe`.

<br/>
<br/>
<br/>

## Disclaimer

This project is for educational and study purposes only. It violates Riot Games' Terms of Service, and we do not encourage or endorse its use in any way that infringes on those terms. 

Use this software at your own risk.
