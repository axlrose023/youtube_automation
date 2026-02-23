from pydantic import BaseModel, HttpUrl


class OpenSiteRequest(BaseModel):
    url: HttpUrl


class OpenSiteResponse(BaseModel):
    status: str
    url: str
