# Adapted from AiriCore plugins/airi_status (MIT License)
from pydantic import BaseModel, Field


class ScopedConfig(BaseModel):
    only_superuser: bool = Field(default=False)
    to_me: bool = Field(default=False)


class Config(BaseModel):
    status: ScopedConfig = Field(default_factory=ScopedConfig)


config = ScopedConfig()
