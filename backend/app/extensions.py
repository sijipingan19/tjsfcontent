"""Independent S1/S3 API: never widen the frozen five-algorithm contract."""
from datetime import datetime
import hashlib
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from recommender import contracts
from recommender.business import read_history
from recommender.data import ContractError
from recommender.student.events import parse_time, utc_now
from recommender.student.consumer import StateStore, short_term
from recommender.student.rrf import fuse, rerank
from .config import DATASET, SPLIT
from .errors import APIError
from .schemas import IdPath, Input, identifier
from .security import ActorDependency

router = APIRouter(tags=['学生标准版扩展'])


class ExtensionQuery(Input):
    limit: int = Field(default=10, ge=1, le=50)


class ExtensionItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    movie_id: str
    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    recall_sources: list[str]
    recall_ranks: dict[str, int]


class ExtensionResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    algorithm: Literal['rrf', 'realtime']
    model_version: str
    generated_at: datetime
    subject_key: str
    strategy: str
    source_models: dict[str, str]
    weights: dict[str, float]
    items: list[ExtensionItem]


class RealtimeItem(ExtensionItem):
    base_rank: int
    base_score: float
    short_term_boost: float


class RealtimeResult(ExtensionResult):
    algorithm: Literal['realtime']
    items: list[RealtimeItem]
    short_term: dict[str, float]
    last_event_id: str | None
    consumer_status: str
    state_status: str
    base_movie_ids: list[str]
    ranking_changed: bool


class EventsResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject_key: str
    enabled: bool
    consumer_status: str
    state_status: str
    last_event_id: str | None
    last_event_at: str | None
    short_term: dict[str, float]
    recent_events: list[dict]


def consumer_status(config):
    if not config.enabled:
        return 'disabled'
    try:
        value = json.loads((config.runtime / '_consumer.json').read_text(encoding='utf-8'))
        if value.get('status') not in {'running', 'stopped', 'failed'}:
            return 'unavailable'
        if (utc_now() - parse_time(value['last_poll_at'])).total_seconds() > 30:
            return 'stale'
        return value['status']
    except FileNotFoundError:
        return 'not_started'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 'unavailable'


def event_view(request, actor, catalog):
    config = request.app.state.event_publisher.config
    subject = f'app:{actor.account_id}'
    view = {'subject_key': subject, 'enabled': config.enabled, 'consumer_status': consumer_status(config),
            'state_status': 'disabled' if not config.enabled else 'no_events', 'last_event_id': None,
            'last_event_at': None, 'short_term': {}, 'recent_events': []}
    if not config.enabled:
        return view
    try:
        state = StateStore(config.runtime).load(subject)
        view.update(last_event_id=state['last_event_id'], last_event_at=state['last_event_at'],
                    short_term=short_term(state, catalog=catalog), recent_events=state['recent_events'])
        if state['last_event_id'] is not None:
            view['state_status'] = 'ready'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        view['state_status'] = 'unavailable'
    return view


def business_catalog(request):
    with request.app.state.db.connection() as conn:
        return {str(mid): (genres or '').split('|') for mid, genres in conn.execute(text('SELECT movie_id,genres FROM movies'))}


def build_rrf(request, kind, uid, limit):
    context = contracts.RecommendationRequest(kind, uid, 50, 'business', DATASET, SPLIT)
    try:
        history = read_history(context, database=request.app.state.db)
        results = {name: contracts.recommend(contracts.Algorithm(name), context) for name in ('content', 'itemcf')}
    except contracts.AlgorithmNotImplemented:
        raise APIError(501, 'EXTENSION_SOURCE_NOT_IMPLEMENTED', 'RRF需要已实现的content与itemcf两个真实候选源') from None
    except ContractError as exc:
        raise APIError(503, exc.code, str(exc)) from None
    catalog = business_catalog(request)
    excluded = {feedback.movie_id for feedback in history.ratings}
    weights = {'content': 1.0, 'itemcf': 1.0}
    items = fuse({name: [item.movie_id for item in result.items] for name, result in results.items()},
                 weights=weights, excluded=excluded, catalog=set(catalog), limit=limit)
    models = {name: result.model_version for name, result in results.items()}
    binding = ';'.join(name+':'+version for name, version in models.items())
    version = 'student-rrf-v1-' + hashlib.sha256(binding.encode()).hexdigest()[:12]
    return {'algorithm': 'rrf', 'model_version': version, 'generated_at': utc_now(),
            'subject_key': f'{kind}:{uid}', 'strategy': 'weighted_rrf',
            'source_models': models, 'weights': weights, 'items': items}, catalog, excluded


@router.get('/me/extensions/rrf', response_model=ExtensionResult)
def my_rrf(request: Request, actor: ActorDependency, query: Annotated[ExtensionQuery, Query()]):
    result, _, _ = build_rrf(request, 'app', actor.account_id, query.limit)
    return result


@router.get('/demo/history-users/{ml_user_id}/extensions/rrf', response_model=ExtensionResult)
def history_rrf(ml_user_id: IdPath, request: Request, query: Annotated[ExtensionQuery, Query()]):
    uid = identifier(ml_user_id)
    with request.app.state.db.connection() as conn:
        if conn.execute(text('SELECT ml_user_id FROM history_users WHERE ml_user_id=:id'), {'id': uid}).first() is None:
            raise APIError(404, 'HISTORY_USER_NOT_FOUND', '历史匿名用户不存在')
    result, _, _ = build_rrf(request, 'ml', uid, query.limit)
    return result


@router.get('/me/extensions/realtime', response_model=RealtimeResult)
def my_realtime(request: Request, actor: ActorDependency, query: Annotated[ExtensionQuery, Query()]):
    base, catalog, excluded = build_rrf(request, 'app', actor.account_id, 50)
    view = event_view(request, actor, catalog)
    profile = view['short_term']
    if not view['enabled']:
        strategy = 'static_fallback_disabled'
    elif view['state_status'] == 'unavailable':
        strategy = 'static_fallback_state_unavailable'
    elif view['last_event_id'] is None:
        strategy = 'static_fallback_no_events'
    elif not profile:
        strategy = 'static_fallback_no_active_interest'
    elif view['consumer_status'] == 'running':
        strategy = 'event_driven_genre_rerank'
    else:
        strategy = 'cached_event_genre_rerank'
    items = rerank(base['items'], profile, catalog=catalog, excluded=excluded, limit=query.limit)
    before = [item['movie_id'] for item in base['items'][:query.limit]]
    return dict(base, algorithm='realtime', model_version=base['model_version']+'-genres-v1',
                strategy=strategy, items=items, short_term=profile, last_event_id=view['last_event_id'],
                consumer_status=view['consumer_status'], state_status=view['state_status'],
                base_movie_ids=before, ranking_changed=before != [item['movie_id'] for item in items])


@router.get('/me/extensions/events', response_model=EventsResult)
def my_events(request: Request, actor: ActorDependency, query: Annotated[Input, Query()]):
    return event_view(request, actor, business_catalog(request))
