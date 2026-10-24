"""Mapping tables loaded from the package resources."""

import tomllib
from functools import cache
from importlib.resources import files


@cache
def load_table(name: str) -> dict[str, object]:
    text = files("cvopt.resources").joinpath(f"{name}.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)
