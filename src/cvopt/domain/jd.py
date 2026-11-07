import math
from typing import Self

from pydantic import field_validator, model_validator

from cvopt.domain.base import CefrLevel, Model
from cvopt.domain.enums import DegreeLevel, Importance, RequirementCategory


class Requirement(Model):
    id: str  # "R01", "R02", ... in JD order
    text: str
    category: RequirementCategory
    importance: Importance
    keywords: list[str]  # the JD's own spelling
    synonyms: list[str]
    years: float | None = None  # years_experience
    language_code: str | None = None  # language
    language_level: CefrLevel | None = None  # A1–C2 or native
    degree_level: DegreeLevel | None = None  # education
    degree_fields: list[str] = []
    certificate_names: list[str] = []  # certification

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Requirement IDs must be nonblank and have no surrounding whitespace.")
        return value

    @field_validator("years")
    @classmethod
    def valid_years(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError("Required years must be positive and finite.")
        return value


class JobDescription(Model):
    title: str
    company: str
    language: str
    requirements: list[Requirement]
    logistics: list[str]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        ids = [r.id for r in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement IDs must be unique.")
        return self
