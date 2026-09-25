"""Operational alerts delivered by the installed Stash Slack bot."""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import worker_ready

from ..config import settings
from ..integrations.slack import client, installs

logger = logging.getLogger(__name__)


def alerts_enabled() -> bool:
    # Both halves of the destination must be set: a half-configured install
    # reads as disabled, not half-enabled.
    return bool(settings.ALERT_SLACK_TEAM_ID and settings.ALERT_SLACK_CHANNEL_ID)


@worker_ready.connect
def _announce_alert_mode(**_: Any) -> None:
    if alerts_enabled():
        logger.info(
            "operational alerts: delivering to Slack team %s, channel %s",
            settings.ALERT_SLACK_TEAM_ID,
            settings.ALERT_SLACK_CHANNEL_ID,
        )
        return
    logger.info(
        "operational alerts disabled: ALERT_SLACK_TEAM_ID and ALERT_SLACK_CHANNEL_ID "
        "are unset, so the stall and curator watchdogs record their decisions without delivering"
    )


async def send_alert(text: str) -> None:
    logger.error("ALERT: %s", text)
    if not alerts_enabled():
        raise RuntimeError("ALERT_SLACK_TEAM_ID and ALERT_SLACK_CHANNEL_ID are required")
    install = await installs.get_install(settings.ALERT_SLACK_TEAM_ID)
    if install is None:
        raise RuntimeError("The alert workspace has no Stash Slack bot installation")
    await client.post_message(install["bot_token"], settings.ALERT_SLACK_CHANNEL_ID, text)
