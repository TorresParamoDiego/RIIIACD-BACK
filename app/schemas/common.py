from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Los nombres de campo viajan en camelCase, igual que en los tipos del frontend."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ErrorDetail(BaseModel):
    codigo: str
    mensaje: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail
