from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any


@dataclass(frozen=True)
class SmdSolderProfile:
    height: float
    terminal_size: tuple[float, float]
    terminal_offset: tuple[float, float] = (0.0, 0.0)
    pad_size: tuple[float, float] | None = None


def parse_solder_pair(key: str, value: Any, positive: bool = True) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not isfinite(item)
            or (positive and item <= 0)
            for item in value
        )
    ):
        qualifier = "positive finite" if positive else "finite"
        raise ValueError(f"{key} must contain two {qualifier} numbers")
    return float(value[0]), float(value[1])


def parse_solder_profiles(data: Any) -> dict[str, tuple[SmdSolderProfile, ...]]:
    if not isinstance(data, dict):
        raise ValueError("solder must be a TOML table")
    profiles = {}
    for reference, entries in data.items():
        rules = entries if isinstance(entries, list) else [entries]
        if not rules:
            raise ValueError(f"solder.{reference} must contain at least one rule")
        parsed = []
        for rule in rules:
            prefix = f"solder.{reference}"
            if not isinstance(rule, dict):
                raise ValueError(f"{prefix} must be a table or array of tables")
            unknown = set(rule) - {"height", "terminal_size", "terminal_offset", "pad_size"}
            if unknown:
                raise ValueError(f"unknown {prefix} options: {sorted(unknown)}")
            height = rule.get("height")
            if (
                isinstance(height, bool)
                or not isinstance(height, (int, float))
                or not isfinite(height)
                or height <= 0
            ):
                raise ValueError(f"{prefix}.height must be a positive finite number")

            parsed.append(
                SmdSolderProfile(
                    float(height),
                    parse_solder_pair(f"{prefix}.terminal_size", rule.get("terminal_size")),
                    parse_solder_pair(
                        f"{prefix}.terminal_offset", rule.get("terminal_offset", [0.0, 0.0]), False
                    ),
                    parse_solder_pair(f"{prefix}.pad_size", rule["pad_size"])
                    if "pad_size" in rule
                    else None,
                )
            )
        profiles[reference] = tuple(parsed)
    return profiles


def match_solder_profile(
    profiles: dict[str, tuple[SmdSolderProfile, ...]], pad_name: str, size: tuple[float, float]
) -> SmdSolderProfile | None:
    parts = pad_name.rsplit("_", 3)
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        return None
    rules = profiles.get(parts[1], ())
    matches = [
        rule
        for rule in rules
        if rule.pad_size is not None
        and all(
            isclose(actual, expected, rel_tol=0, abs_tol=1e-4)
            for actual, expected in zip(size, rule.pad_size)
        )
    ]
    if not matches:
        matches = [rule for rule in rules if rule.pad_size is None]
    if len(matches) > 1:
        raise ValueError(f"ambiguous solder rules for pad {pad_name}")
    return matches[0] if matches else None
