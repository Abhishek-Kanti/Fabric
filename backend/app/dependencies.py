"""Application dependency injection providers for Phase 0."""

from typing import Annotated
from fastapi import Depends

from app.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
