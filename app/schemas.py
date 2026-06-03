from pydantic import BaseModel


class SubmitRequest(BaseModel):
    url: str


class SubmitResponse(BaseModel):
    short_id: str
    url: str


class StatusResponse(BaseModel):
    status: str
    error: str | None
