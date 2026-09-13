"""Declared settings schema for config.json.

Single place that lists which configuration keys the app knows about,
derived from DEFAULT_CONFIG in src/constants.py. Also provides
validate_config_dict, which reports unknown keys and wrong-typed values
without raising and without touching the input, so the regular default
merge in src/config.py keeps its existing semantics (user values win).

Value-level validation for the "rpc_overrides" section (rank/agent names,
whole numbers) is owned by RpcOverrides.from_config in
src/rpc_payload.py, the single validation home, which warns once at
startup; this module only checks the section's structure.
"""
from src.constants import DEFAULT_CONFIG

KNOWN_TOP_KEYS = frozenset(DEFAULT_CONFIG.keys())
KNOWN_TABLE_FLAGS = frozenset(DEFAULT_CONFIG.get("table", {}).keys())
KNOWN_FLAGS = frozenset(DEFAULT_CONFIG.get("flags", {}).keys())
KNOWN_RPC_OVERRIDES = frozenset(DEFAULT_CONFIG.get("rpc_overrides", {}).keys())


def _type_name(value):
    return type(value).__name__


def _known_type(key, value):
    """True when value's type matches the declared default's type."""
    default = DEFAULT_CONFIG.get(key)
    return type(value) is type(default)


def _known_table_type(key, value):
    default = DEFAULT_CONFIG["table"].get(key)
    return type(value) is type(default)


def _known_flag_type(key, value):
    default = DEFAULT_CONFIG["flags"].get(key)
    return type(value) is type(default)


def validate_config_dict(config, log):
    """Log one clear line per unknown key or wrong-typed value.

    Never raises and never mutates: the input dict is only read. Wrong-typed
    values are logged and kept, because the default merge in src/config.py
    runs after this and gives user values precedence.
    """
    for key, value in config.items():
        if key == "table":
            if not isinstance(value, dict):
                log(f'config: wrong type for "table" (expected dict, got {_type_name(value)}); value kept as-is')
                continue
            for tkey, tvalue in value.items():
                if tkey not in KNOWN_TABLE_FLAGS:
                    log(f'config: unknown table key "{tkey}" ignored by accessors')
                elif not _known_table_type(tkey, tvalue):
                    log(f'config: wrong type for table "{tkey}" (expected {_type_name(DEFAULT_CONFIG["table"][tkey])}, got {_type_name(tvalue)}); value kept as-is')
        elif key == "flags":
            if not isinstance(value, dict):
                log(f'config: wrong type for "flags" (expected dict, got {_type_name(value)}); value kept as-is')
                continue
            for fkey, fvalue in value.items():
                if fkey not in KNOWN_FLAGS:
                    log(f'config: unknown flag "{fkey}" ignored by accessors')
                elif not _known_flag_type(fkey, fvalue):
                    log(f'config: wrong type for flag "{fkey}" (expected {_type_name(DEFAULT_CONFIG["flags"][fkey])}, got {_type_name(fvalue)}); value kept as-is')
        elif key == "rpc_overrides":
            # Structure only (section type + known subkeys). Value validity
            # (rank/agent names, whole numbers) is owned by
            # RpcOverrides.from_config in src/rpc_payload.py, which warns
            # once per invalid value at startup.
            if not isinstance(value, dict):
                log(f'config: wrong type for "rpc_overrides" (expected dict, got {_type_name(value)}); value kept as-is')
                continue
            for rkey in value:
                if rkey not in KNOWN_RPC_OVERRIDES:
                    log(f'config: unknown rpc_overrides key "{rkey}" ignored by accessors')
        elif key in KNOWN_TOP_KEYS:
            if not _known_type(key, value):
                log(f'config: wrong type for "{key}" (expected {_type_name(DEFAULT_CONFIG[key])}, got {_type_name(value)}); value kept as-is')
        else:
            log(f'config: unknown top-level key "{key}" ignored by accessors')
