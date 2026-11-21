"""JSON-only command line interface for the WorkElate foundation."""

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from workelate.config import load_settings
from workelate.logging_config import configure_logging
from workelate.ingestion.errors import IngestionError
from workelate.ingestion.service import ingest_day
from workelate.placeholders import execute
from workelate.schemas import AskRequest, BriefRequest, ForgetRequest, IngestRequest


class ArgumentError(Exception):
    """Expected parser failure; do not echo potentially private input."""


class JSONHelp(Exception):
    def __init__(self, text: str):
        self.text = text


class JSONParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentError

    def print_help(self, file=None) -> None:
        raise JSONHelp(self.format_help())


def build_parser() -> argparse.ArgumentParser:
    parser = JSONParser(prog="assist", description="WorkElate CLI: ingestion and assistant foundation", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", allow_abbrev=False)
    ingest.add_argument("--day", required=True, type=int)
    brief = commands.add_parser("brief", allow_abbrev=False)
    brief.add_argument("--person", required=True)
    brief.add_argument("--date", required=True)
    ask = commands.add_parser("ask", allow_abbrev=False)
    ask.add_argument("--person", required=True)
    ask.add_argument("--at", required=True)
    ask.add_argument("--ask", required=True)
    forget = commands.add_parser("forget", allow_abbrev=False)
    forget.add_argument("--person", required=True)
    forget.add_argument("--what", required=True)
    return parser


def emit(payload: dict | list) -> None:
    print(json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging("WARNING")
    logger = logging.getLogger("workelate")
    try:
        args = vars(build_parser().parse_args(argv))
        command = args.pop("command")
        schema = {"ingest": IngestRequest, "brief": BriefRequest, "ask": AskRequest, "forget": ForgetRequest}[command]
        request = schema.model_validate(args)
        settings = load_settings()
        configure_logging(settings.log_level)
        if command == "ingest":
            payload = ingest_day(request.day, settings)
            emit(payload)
            return 0
        result = execute(command, request, settings)
    except JSONHelp as help_request:
        emit({"help": help_request.text})
        return 0
    except IngestionError as exc:
        logger.error("Ingestion failed: %s", exc.code)
        emit(exc.as_dict())
        return 4
    except (ArgumentError, ValidationError):
        logger.error("Invalid command or arguments. Use --help for usage.")
        emit({"error": "validation_error", "message": "Invalid command or arguments. Use --help for usage."})
        return 2
    except ValueError:
        logger.error("Invalid configuration.")
        emit({"error": "configuration_error"})
        return 2
    except Exception:
        logger.error("Unexpected internal error.")
        emit({"error": "internal_error"})
        return 1
    emit(result.payload)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
