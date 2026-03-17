from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.analytics import get_analytics

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_view(request: Request, db: Session = Depends(get_db)):
    data = get_analytics(db)
    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "page_title": "Analytics",
        "data": data,
    })
