"""Usage / quota reporting for the dashboard."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from app import db
from app.config import get_settings
from app.security import require_dashboard
from app.services.quota import QuotaGuard

router = APIRouter(prefix="/api/usage", tags=["usage"], dependencies=[Depends(require_dashboard)])


@router.get("")
async def usage() -> dict[str, Any]:
    settings = get_settings()
    snapshot = await QuotaGuard(settings).snapshot()

    days: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    totals = {"runs": 0, "comments": 0, "tokens_in": 0, "tokens_out": 0}
    if db.is_configured():
        async with db.session_scope() as session:
            rows = (
                await session.execute(
                    sa.text(
                        """
                        SELECT day, llm_calls, tokens_in, tokens_out, runs
                        FROM usage_daily
                        ORDER BY day DESC
                        LIMIT 14
                        """
                    )
                )
            ).all()
            days = [
                {
                    "day": row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]),
                    "llm_calls": int(row[1]),
                    "tokens_in": int(row[2]),
                    "tokens_out": int(row[3]),
                    "runs": int(row[4]),
                }
                for row in rows
            ]
            status_rows = (
                await session.execute(
                    sa.text("SELECT status, count(*) FROM review_runs GROUP BY status")
                )
            ).all()
            by_status = {str(row[0]): int(row[1]) for row in status_rows}
            summary = (
                await session.execute(
                    sa.text(
                        """
                        SELECT count(*), coalesce(sum(comments_posted), 0),
                               coalesce(sum(tokens_in), 0), coalesce(sum(tokens_out), 0)
                        FROM review_runs
                        """
                    )
                )
            ).first()
            if summary is not None:
                totals = {
                    "runs": int(summary[0]),
                    "comments": int(summary[1]),
                    "tokens_in": int(summary[2]),
                    "tokens_out": int(summary[3]),
                }

    return {
        "today": snapshot,
        "limits": {
            "rpm": settings.llm_rpm,
            "rpd": settings.llm_rpd,
            "daily_budget": settings.daily_llm_budget,
            "review_mode": settings.review_mode,
            "max_agent_steps": settings.max_agent_steps,
            "provider": settings.llm_provider,
            "model_configured": bool(settings.llm_model),
        },
        "days": days,
        "by_status": by_status,
        "totals": totals,
    }
