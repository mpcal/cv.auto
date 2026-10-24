from cvopt.domain.base import Model
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
    language_level: str | None = None  # A1–C2 or native
    degree_level: DegreeLevel | None = None  # education
    degree_fields: list[str] = []
    certificate_names: list[str] = []  # certification


class JobDescription(Model):
    title: str
    company: str
    language: str
    requirements: list[Requirement]
    logistics: list[str]
