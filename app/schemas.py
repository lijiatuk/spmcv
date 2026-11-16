from pydantic import BaseModel, ConfigDict, Field


class Conclusion(BaseModel):
    action: str | None = None
    label: str | None = None


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    scene: str
    scene_confidence: float | None = None
    conclusion: Conclusion | None = None
    evidence: dict = Field(default_factory=dict)
    fields: dict | None = None
    need_review: bool = True
    review_reasons: list[str] = Field(default_factory=list)
    statement: str = ""
    work_order_id: str = ""
