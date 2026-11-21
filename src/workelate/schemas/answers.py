"""Official ask output; model selection is a separate internal format."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class AnswerText(StrictModel):
    text: str
    sources: list[str]


class Match(StrictModel):
    phrase: str
    id: str
    sources: list[str]


class Option(StrictModel):
    id: str
    text: str


class Clarify(StrictModel):
    question: str
    options: list[Option]


class Refused(StrictModel):
    reason: str


class AskUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = 0.0
    ms: int = Field(default=0, ge=0)


class AskResponse(StrictModel):
    answer: AnswerText | None = None
    matched: list[Match] = Field(default_factory=list)
    clarify: Clarify | None = None
    refused: Refused | None = None
    usage: AskUsage = Field(default_factory=AskUsage)

    @model_validator(mode="after")
    def one_outcome(self):
        if sum(value is not None for value in (self.answer, self.clarify, self.refused)) != 1:
            raise ValueError("Exactly one ask outcome is required")
        return self


class SelectedClaim(StrictModel):
    evidence_id: str
    text: str
    sources: list[str] = Field(min_length=1)


class GeneratedAnswer(StrictModel):
    claims: list[SelectedClaim] = Field(max_length=6)
