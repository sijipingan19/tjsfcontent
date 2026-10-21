from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Algorithm(StrEnum):
    popular = "popular"
    content = "content"
    itemcf = "itemcf"
    als = "als"
    cold_start = "cold_start"


@dataclass(frozen=True)
class RecommendationRequest:
    subject_kind: Literal["app", "ml"]
    subject_id: int
    limit: int
    runtime_mode: Literal["business", "evaluation"]
    dataset_version: str
    split_version: str


class RecommendationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movie_id: str
    rank: int = Field(ge=1)
    algorithm: Algorithm
    model_version: str = Field(min_length=1)
    score: float | None = Field(default=None, allow_inf_nan=False)
    recall_sources: list[str] | None = None
    recall_ranks: dict[str, int] | None = None


class RecommendationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ready"] = "ready"
    algorithm: Algorithm
    model_version: str = Field(min_length=1)
    dataset_version: str
    split_version: str
    runtime_mode: Literal["business", "evaluation"]
    batch_id: str | None = None
    generated_at: datetime
    items: list[RecommendationItem]


class Recommender(Protocol):
    model_manifest_path: str | Path
    def recommend(self, request: RecommendationRequest) -> RecommendationResult: ...


class AlgorithmNotImplemented(Exception):
    pass


# Students implement one provider per task and explicitly register it here.
# An empty registry deliberately produces no movie list or model result.
PROVIDERS: dict[Algorithm, Recommender] = {}


def recommend(algorithm: Algorithm, request: RecommendationRequest, context: dict | None = None) -> RecommendationResult:
    provider = PROVIDERS.get(algorithm)
    if provider is None:
        raise AlgorithmNotImplemented(algorithm.value)
    from .data import FrozenData, require
    from .validation import check_model, check_provider, check_request, check_result
    check_request(request)
    check_provider(provider)
    model_path = getattr(provider, "model_manifest_path", None)
    require(isinstance(model_path, (str, Path)),
            "已注册Provider须指定model_manifest_path", "MODEL_BINDING_INVALID")
    data = FrozenData()
    model = check_model(model_path, request.runtime_mode, algorithm.value, data)
    import inspect
    params = inspect.signature(provider.recommend).parameters
    accepts_context = len(params) >= 2 or any(p.kind is p.VAR_KEYWORD for p in params.values())
    raw = provider.recommend(request, context) if accepts_context else provider.recommend(request)
    return check_result(raw, request, algorithm, data.catalog(), model.model_version)


# 学生实现：只注册延迟加载实例；导入不训练、不读模型、不访问数据库。
from .student.providers import StudentProvider

PROVIDERS[Algorithm.popular] = StudentProvider('popular', Path('models/business/popular-v1/manifest.json'))
PROVIDERS[Algorithm.content] = StudentProvider('content', Path('models/business/content-v1/manifest.json'))
PROVIDERS[Algorithm.itemcf] = StudentProvider('itemcf', Path('models/business/itemcf-v1/manifest.json'))
PROVIDERS[Algorithm.als] = StudentProvider('als', Path('models/business/als-v1/manifest.json'))
PROVIDERS[Algorithm.cold_start] = StudentProvider('cold_start', Path('models/business/cold_start-v1/manifest.json'))
