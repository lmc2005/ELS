from pydantic import BaseModel, Field


class VocabImageResult(BaseModel):
    image_url: str
    thumbnail_url: str
    source: str
    license: str
    description: str
    relevance_score: int | None = None


class VocabExample(BaseModel):
    sentence: str
    note: str = ""


class VocabImageOut(BaseModel):
    word: str
    definition: str = ""
    part_of_speech: str = ""
    usage_note: str = ""
    examples: list[VocabExample] = Field(default_factory=list)
    results: list[VocabImageResult]
