"""Instance Jinja2Templates partagée entre les routeurs."""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
