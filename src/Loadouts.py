import time
import requests
from colr import color
from src.constants import sockets, hide_names
from src import static_content
import json


# ---------------------------------------------------------------------------
# Catalog lookup indexes
#
# The valorant-api catalogs (/v1/weapons, /v1/skins, /v1/buddies, ...) are flat
# "data" lists that the loadout conversion used to re-scan linearly for every
# player and every inventory item on every build (~8s per INGAME build in
# production log-615). The builders below flatten each catalog once; results
# are cached on the catalog object's identity, so a fresh catalog payload (a
# new object returned by static_content) rebuilds automatically.
_INDEX_CACHE = {}  # id(payload) -> {"payload": payload, "indexes": {name: index}}


def _catalog_index(payload, index_name, builder):
    """Lookup index for a catalog payload, built once and cached by identity."""
    entry = _INDEX_CACHE.get(id(payload))
    if entry is None or entry["payload"] is not payload:
        entry = {"payload": payload, "indexes": {}}
        _INDEX_CACHE[id(payload)] = entry
    index = entry["indexes"].get(index_name)
    if index is None:
        index = builder(payload)
        entry["indexes"][index_name] = index
    return index


def _weapons_by_name_lower(payload):
    """lower(displayName) -> [weapon dicts] in catalog order.

    A list per name: the old loop processed every weapon whose name matched,
    each processing updating the same dict key (last one winning), so the
    list order must be preserved to reproduce the final value exactly."""
    index = {}
    for weapon in payload["data"]:
        index.setdefault(weapon["displayName"].lower(), []).append(weapon)
    return index


def _uuid_index(payload):
    """uuid -> entry, exact case (the conversion loops compared uuids with ==)."""
    return {entry["uuid"]: entry for entry in payload["data"]}


def _uuid_lower_index(payload):
    """lower(uuid) -> entry (the skins/sprays loops lowercased both sides)."""
    return {entry["uuid"].lower(): entry for entry in payload["data"]}


def _weapon_skin_tree(payload):
    """One pass over /v1/weapons for the per-item O(1) walk.

    Skins are scoped to their weapon and chromas to their skin so a uuid can
    only resolve inside the same nesting the old nested loops enforced."""
    weapons_by_uuid = {}
    skins_by_weapon_uuid = {}
    chromas_by_skin_uuid = {}
    for weapon in payload["data"]:
        weapons_by_uuid[weapon["uuid"]] = weapon
        skins = {}
        skins_by_weapon_uuid[weapon["uuid"]] = skins
        for skin in weapon["skins"]:
            skins[skin["uuid"]] = skin
            chromas = {}
            chromas_by_skin_uuid[skin["uuid"]] = chromas
            for chroma in skin["chromas"]:
                chromas[chroma["uuid"]] = chroma
    return {"weapons_by_uuid": weapons_by_uuid,
            "skins_by_weapon_uuid": skins_by_weapon_uuid,
            "chromas_by_skin_uuid": chromas_by_skin_uuid}


class Loadouts:
    def __init__(self, Requests, log, colors, Server, current_map):

        self.Requests = Requests
        self.log = log
        self.colors = colors
        self.Server = Server
        self.current_map = current_map

    def get_match_loadouts(self, match_id, players, weaponChoose, valoApiSkins, names, state="game"):
        playersBackup = players
        weaponLists = {}
        phantomLists = {}
        valApiWeapons = static_content.get_json("/v1/weapons")
        if valApiWeapons is None:
            raise ConnectionError("valorant-api.com /v1/weapons unavailable")
        if state == "game":
            team_id = "Blue"
            PlayerInventorys = self.Requests.fetch("glz", f"/core-game/v1/matches/{match_id}/loadouts", "get")
        elif state == "pregame":
            pregame_stats = players
            players = players["AllyTeam"]["Players"]
            team_id = pregame_stats['Teams'][0]['TeamID']
            PlayerInventorys = self.Requests.fetch("glz", f"/pregame/v1/matches/{match_id}/loadouts", "get")
        if not isinstance(PlayerInventorys, dict) or "Loadouts" not in PlayerInventorys:
            # fetch() returns None for an empty/non-JSON loadouts body (seen
            # in production at match boundaries). Degrade to empty loadouts
            # instead of crashing on the missing "Loadouts" key in the loop
            # below or in main.py's heartbeat (loadouts_data["Players"]).
            self.log(f"loadouts: empty/non-JSON loadouts response for match {match_id} (state={state}), using empty loadouts")
            empty_entries = {
                p["Subject"]: {"Agent": None, "Team": p.get("TeamID"), "Sprays": None,
                                "Title": None, "PlayerCard": None, "Weapons": None}
                for p in players if isinstance(p, dict) and p.get("Subject")
            }
            return [{}, {}, {"Players": empty_entries, "time": int(time.time()), "map": self.current_map}]
        # O(1) catalog lookups built once per catalog payload: the old code
        # re-scanned every weapon and every skin for every player per build.
        # Built after the empty-loadouts early return, exactly where the old
        # loops first touched the catalogs (valoApiSkins may be None up here).
        weapons_by_name = _catalog_index(valApiWeapons, "by_name_lower", _weapons_by_name_lower)
        skins_by_uuid = _catalog_index(valoApiSkins, "by_uuid_lower", _uuid_lower_index)
        chosen_key = weaponChoose.lower()
        chosen_weapons = weapons_by_name.get(chosen_key, [])
        # old elif: the phantom branch only ran when the chosen name missed
        phantom_weapons = [] if chosen_key == "phantom" else weapons_by_name.get("phantom", [])
        for player in range(len(players)):
            if team_id == "Red":
                invindex = player + len(players) - len(PlayerInventorys["Loadouts"])
            else:
                invindex = player
            inv = PlayerInventorys["Loadouts"][invindex]
            if state == "game":
                inv = inv["Loadout"]
            for weapon in chosen_weapons:
                skin_id = \
                    inv["Items"][weapon["uuid"].lower()]["Sockets"]["bcef87d6-209b-46c6-8b19-fbe40bd95abc"]["Item"][
                        "ID"]
                skin = skins_by_uuid.get(skin_id.lower())
                if skin is not None:
                    rgb_color = self.colors.get_rgb_color_from_skin(skin["uuid"].lower(), valoApiSkins)
                    skin_display_name = skin["displayName"].replace(f" {weapon['displayName']}", "")
                    # if rgb_color is not None:
                    weaponLists.update({players[player]["Subject"]: color(skin_display_name, fore=rgb_color)})
                    # else:
                    #     weaponLists.update({player["Subject"]: color(skin["Name"], fore=rgb_color)})
            for weapon in phantom_weapons:
                skin_id = \
                    inv["Items"][weapon["uuid"].lower()]["Sockets"]["bcef87d6-209b-46c6-8b19-fbe40bd95abc"]["Item"][
                        "ID"]
                skin = skins_by_uuid.get(skin_id.lower())
                if skin is not None:
                    rgb_color = self.colors.get_rgb_color_from_skin(skin["uuid"].lower(), valoApiSkins)
                    skin_display_name = skin["displayName"].replace(f" {weapon['displayName']}", "")
                    phantomLists.update({players[player]["Subject"]: color(skin_display_name, fore=rgb_color)})
        final_json = self.convertLoadoutToJsonArray(PlayerInventorys, playersBackup, state, names)
        # self.log(f"json for website: {final_json}")
        self.Server.send_payload("matchLoadout",final_json)
        return [weaponLists, phantomLists, final_json]

    #this will convert valorant loadouts to json with player names
    def convertLoadoutToJsonArray(self, PlayerInventorys, players, state, names):
        #get agent dict from main in future
        # names = self.namesClass.get_names_from_puuids(players)
        # Static payloads come from the process-wide cache (one fetch per
        # path per run); a cache failure raises exactly like the old direct
        # requests.get() did on a network error.
        def _cached(api_path):
            payload = static_content.get_json(api_path)
            if payload is None:
                raise ConnectionError(f"valorant-api.com {api_path} unavailable")
            return payload

        valoApiSprays = _cached("/v1/sprays")
        valoApiWeapons = _cached("/v1/weapons")
        valoApiBuddies = _cached("/v1/buddies")
        valoApiAgents = _cached("/v1/agents")
        valoApiTitles = _cached("/v1/playertitles")
        valoApiPlayerCards = _cached("/v1/playercards")

        # O(1) catalog lookups built once per catalog payload: the old code
        # re-scanned agents, titles, player cards, sprays, buddies and the
        # weapon/skin/chroma tree for every player and item per build.
        agents_by_uuid = _catalog_index(valoApiAgents, "by_uuid", _uuid_index)
        titles_by_uuid = _catalog_index(valoApiTitles, "by_uuid", _uuid_index)
        player_cards_by_uuid = _catalog_index(valoApiPlayerCards, "by_uuid", _uuid_index)
        buddies_by_uuid = _catalog_index(valoApiBuddies, "by_uuid", _uuid_index)
        sprays_by_uuid = _catalog_index(valoApiSprays, "by_uuid_lower", _uuid_lower_index)
        weapon_tree = _catalog_index(valoApiWeapons, "weapon_skin_tree", _weapon_skin_tree)

        final_final_json = {"Players": {},
                            "time": int(time.time()),
                            "map": self.current_map}

        final_json = final_final_json["Players"]
        if state == "game":
            PlayerInventorys = PlayerInventorys["Loadouts"]
            for i in range(len(PlayerInventorys)):
                if i >= len(players):
                    break
                PlayerInventory = PlayerInventorys[i]["Loadout"]
                final_json.update(
                    {
                        players[i]["Subject"]: {}
                    }
                )

                #creates name field
                if hide_names:
                    agent = agents_by_uuid.get(players[i]["CharacterID"])
                    if agent is not None:
                        final_json[players[i]["Subject"]].update({"Name": agent["displayName"]})
                else:
                    final_json[players[i]["Subject"]].update({"Name": names[players[i]["Subject"]]})

                #creates team field
                final_json[players[i]["Subject"]].update({"Team": players[i]["TeamID"]})

                #create spray field
                final_json[players[i]["Subject"]].update({"Sprays": {}})
                #append sprays to field

                final_json[players[i]["Subject"]].update({"Level": players[i]["PlayerIdentity"]["AccountLevel"]})

                title = titles_by_uuid.get(players[i]["PlayerIdentity"]["PlayerTitleID"])
                if title is not None:
                    final_json[players[i]["Subject"]].update({"Title": title["titleText"]})


                player_card = player_cards_by_uuid.get(players[i]["PlayerIdentity"]["PlayerCardID"])
                if player_card is not None:
                    final_json[players[i]["Subject"]].update({"PlayerCard": player_card["largeArt"]})

                agent = agents_by_uuid.get(players[i]["CharacterID"])
                if agent is not None:
                    final_json[players[i]["Subject"]].update({"AgentArtworkName": agent["displayName"] + "Artwork"})
                    final_json[players[i]["Subject"]].update({"Agent": agent["displayIcon"]})

                spray_selections = [
                    s for s in PlayerInventory.get("Expressions", {}).get("AESSelections", [])
                    if s.get("TypeID") == "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475"
                ]
                for j, spray in enumerate(spray_selections):
                    final_json[players[i]["Subject"]]["Sprays"].update({j: {}})
                    spray_val_api = sprays_by_uuid.get(spray["AssetID"].lower())
                    if spray_val_api is not None:
                        final_json[players[i]["Subject"]]["Sprays"][j].update({
                            "displayName": spray_val_api["displayName"],
                            "displayIcon": spray_val_api["displayIcon"],
                            "fullTransparentIcon": spray_val_api["fullTransparentIcon"]
                        })

                #create weapons field
                final_json[players[i]["Subject"]].update({"Weapons": {}})

                for skin in PlayerInventory["Items"]:

                    #create skin field
                    final_json[players[i]["Subject"]]["Weapons"].update({skin: {}})

                    for socket in PlayerInventory["Items"][skin]["Sockets"]:
                        #predefined sockets
                        for var_socket in sockets:
                            if socket == sockets[var_socket]:
                                final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                    {
                                        var_socket: PlayerInventory["Items"][skin]["Sockets"][socket]["Item"]["ID"]
                                    }
                                )

                    #create buddy field
                    # self.log("predefined sockets")
                    # final_json[players[i]["Subject"]]["Weapons"].update({skin: {}})

                    #buddies
                    item_sockets = PlayerInventory["Items"][skin]["Sockets"]
                    if sockets["skin_buddy"] in item_sockets:
                        buddy = buddies_by_uuid.get(item_sockets[sockets["skin_buddy"]]["Item"]["ID"])
                        if buddy is not None:
                            final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                {
                                    "buddy_displayIcon": buddy["displayIcon"]
                                }
                            )

                    #append names to field
                    weapon = weapon_tree["weapons_by_uuid"].get(skin)
                    if weapon is not None:
                        final_json[players[i]["Subject"]]["Weapons"][skin].update(
                            {
                                "weapon": weapon["displayName"]
                            }
                        )
                        skin_item_id = PlayerInventory["Items"][skin]["Sockets"][sockets["skin"]]["Item"]["ID"]
                        skin_val_api = weapon_tree["skins_by_weapon_uuid"].get(weapon["uuid"], {}).get(skin_item_id)
                        if skin_val_api is not None:
                            final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                {
                                    "skinDisplayName": skin_val_api["displayName"]
                                }
                            )
                            chroma_item_id = PlayerInventory["Items"][skin]["Sockets"][sockets["skin_chroma"]]["Item"]["ID"]
                            chroma = weapon_tree["chromas_by_skin_uuid"].get(skin_val_api["uuid"], {}).get(chroma_item_id)
                            if chroma is not None:
                                if chroma["displayIcon"] != None:
                                    final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                        {
                                            "skinDisplayIcon": chroma["displayIcon"]
                                        }
                                    )
                                elif chroma["fullRender"] != None:
                                    final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                        {
                                            "skinDisplayIcon": chroma["fullRender"]
                                        }
                                    )
                                elif skin_val_api["displayIcon"] != None:
                                    final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                        {
                                            "skinDisplayIcon": skin_val_api["displayIcon"]
                                        }
                                    )
                                else:
                                    final_json[players[i]["Subject"]]["Weapons"][skin].update(
                                        {
                                            "skinDisplayIcon": skin_val_api["levels"][0]["displayIcon"]
                                        }
                                    )
                            if skin_val_api["displayName"].startswith("Standard") or skin_val_api["displayName"].startswith("Melee"):
                                final_json[players[i]["Subject"]]["Weapons"][skin]["skinDisplayIcon"] = weapon["displayIcon"]

        return final_final_json
