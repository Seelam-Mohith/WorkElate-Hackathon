"""Temporary Stage 1 operations, independent of argument parsing.

No operation reads records or creates persistent state. All return exit code 3.
"""

import logging
from dataclasses import dataclass

from workelate.config import Settings
from workelate.schemas import Answer, AskPlaceholder, Request

logger = logging.getLogger("workelate")
NOT_IMPLEMENTED = 3


@dataclass(frozen=True)
class CommandResult:
    payload: dict | list
    exit_code: int


def execute(command: str, request: Request, settings: Settings) -> CommandResult:
    # Validated request and resolved settings are the handoff for later stages.
    logger.warning("%s is not implemented in Stage 1", command)
    if command == "brief":
        return CommandResult([], NOT_IMPLEMENTED)
    if command == "ask":
        payload = AskPlaceholder(
            answer=Answer(text="Question answering is not implemented in Stage 1.")
        ).model_dump()
        return CommandResult(payload, NOT_IMPLEMENTED)
    return CommandResult({"error": "not_implemented", "command": command}, NOT_IMPLEMENTED)
