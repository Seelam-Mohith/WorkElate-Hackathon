"""Public ingestion errors contain locations and safe codes, never source contents."""


class IngestionError(Exception):
    def __init__(self, code: str, *, source: str | None = None, line: int | None = None):
        super().__init__(code)
        self.code = code
        self.source = source
        self.line = line

    def as_dict(self) -> dict:
        result = {"error": self.code, "command": "ingest"}
        if self.source is not None:
            result["source"] = self.source
        if self.line is not None:
            result["line"] = self.line
        return result
