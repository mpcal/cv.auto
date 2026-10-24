from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
CefrLevel = Annotated[str, Field(pattern=r"^(A1|A2|B1|B2|C1|C2|native)$")]
