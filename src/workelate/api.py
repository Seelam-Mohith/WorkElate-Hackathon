"""Local JSON API for the WorkElate Streamlit UI."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from workelate.briefs.service import generate_briefs
from workelate.config import load_settings
from workelate.forgetting.service import forget_information
from workelate.ingestion.errors import IngestionError
from workelate.ingestion.service import database_path, ingest_day
from workelate.reasoning.answer_engine import ask_question
from workelate.schemas import AskRequest, BriefRequest, ForgetRequest, IngestRequest
from workelate.storage.database import connect_database
from workelate.ui_options import person_dates

class BriefApiRequest(BriefRequest):
    pretty: bool = False


class APIHandler(BaseHTTPRequestHandler):
    server_version = "WorkElateAPI/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status, payload):
        body = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, {"ok": True})
        elif path == "/api/people":
            try:
                database = database_path(self.server.settings)
                people = []
                if database.exists():
                    with connect_database(database, read_only=True) as conn:
                        people = [dict(row) for row in conn.execute("SELECT id,name FROM people ORDER BY id")]
                self._send_json(200, people)
            except Exception:
                self._send_json(500, {"error": "people_unavailable"})
        elif len(parts := path.strip("/").split("/")) == 4 and parts[:2] == ["api", "people"] and parts[3] == "dates":
            try:
                self._send_json(200, person_dates(unquote(parts[2]), self.server.settings))
            except Exception:
                self._send_json(500, {"error": "dates_unavailable"})
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        routes = {"/api/ingest": self._ingest, "/api/brief": self._brief,
                  "/api/ask": self._ask, "/api/forget": self._forget}
        handler = routes.get(path)
        if handler is None:
            self._send_json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "validation_error"})
            return
        if length <= 0 or length > 1_000_000:
            self._send_json(400, {"error": "validation_error"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "validation_error"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "validation_error"})
            return
        try:
            self._send_json(200, handler(body))
        except ValidationError:
            self._send_json(400, {"error": "validation_error", "message": "Invalid request"})
        except IngestionError as exc:
            self._send_json(400, exc.as_dict())
        except ValueError:
            self._send_json(400, {"error": "configuration_error"})
        except Exception:
            self._send_json(500, {"error": "internal_error"})

    def _ingest(self, body):
        request = IngestRequest.model_validate(body)
        return ingest_day(request.day, self.server.settings)

    def _brief(self, body):
        request = BriefApiRequest.model_validate(body)
        items = generate_briefs(request.person, request.date, self.server.settings, polish=request.pretty)
        if request.pretty:
            return [{"meeting_id": item["meeting_id"], "paragraph": item["paragraph"], "usage": item["usage"]} for item in items]
        return items

    def _ask(self, body):
        request = AskRequest.model_validate(body)
        return ask_question(request.person, request.at, request.ask, self.server.settings)

    def _forget(self, body):
        request = ForgetRequest.model_validate(body)
        return forget_information(request.person, request.what, self.server.settings)


def main():
    parser = argparse.ArgumentParser(description="WorkElate local JSON API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), APIHandler)
    server.settings = load_settings()
    print(f"WorkElate API: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
