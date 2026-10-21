"""Server-owned events and acknowledged Kafka publication, disabled by default."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
from uuid import UUID, uuid4

log = logging.getLogger('course_events')


@dataclass(frozen=True)
class EventConfig:
    enabled: bool = False
    bootstrap_servers: str = 'kafka:9092'
    topic: str = 'cinelab.activity.v1'
    runtime: Path = Path('/app/runtime/events')
    group_id: str = 'cinelab-short-term-v1'

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('COURSE_EVENTS_ENABLED', 'false').lower() == 'true',
                   bootstrap_servers=os.getenv('COURSE_KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'),
                   topic=os.getenv('COURSE_KAFKA_TOPIC', 'cinelab.activity.v1'),
                   runtime=Path(os.getenv('COURSE_EVENTS_RUNTIME', '/app/runtime/events')),
                   group_id=os.getenv('COURSE_KAFKA_GROUP_ID', 'cinelab-short-term-v1'))


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError('Invalid event timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid event timestamp') from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError('Event timestamp must be UTC')
    return parsed


def valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[1-9][0-9]{0,19}', value)) and int(value) <= 2**64-1


def validate_event(event):
    keys = {'schema_version', 'event_id', 'occurred_at', 'account_id', 'movie_id', 'action', 'rating'}
    if not isinstance(event, dict) or set(event) != keys or type(event['schema_version']) is not int or event['schema_version'] != 1:
        raise ValueError('Invalid event schema')
    try:
        uid = UUID(event['event_id'])
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid event UUID') from None
    if str(uid) != event['event_id'] or uid.version != 4:
        raise ValueError('Expected canonical UUID4')
    timestamp = parse_time(event['occurred_at'])
    if timestamp > utc_now() + timedelta(minutes=5):
        raise ValueError('Event timestamp too far in future')
    account = event['account_id']
    if not isinstance(account, str) or not account.startswith('app:') or not valid_id(account[4:]) or not valid_id(event['movie_id']):
        raise ValueError('Invalid app identity or movie ID')
    action = event['action']
    if action not in {'rating', 'favorite_add', 'favorite_remove'}:
        raise ValueError('Invalid event action')
    if action == 'rating':
        value = event['rating']
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Invalid event rating')
        number = Decimal(str(value))
        if not number.is_finite() or not Decimal('0.5') <= number <= 5 or number * 2 != (number * 2).to_integral_value():
            raise ValueError('Invalid half-star rating')
    elif event['rating'] is not None:
        raise ValueError('Favorites do not carry a rating')
    return event


def make_event(account_id, movie_id, action, rating=None):
    if type(account_id) is not int or type(movie_id) is not int:
        raise ValueError('Server IDs must be integers')
    event = {'schema_version': 1, 'event_id': str(uuid4()), 'occurred_at': utc_now().isoformat(),
             'account_id': f'app:{account_id}', 'movie_id': str(movie_id), 'action': action,
             'rating': None if rating is None else float(rating)}
    return validate_event(event)


class Publisher:
    def __init__(self, config, producer_factory=None):
        self.config = config
        self.producer_factory = producer_factory
        self._producer = None
        self._lock = Lock()

    def publish(self, event):
        validate_event(event)
        if not self.config.enabled:
            return {'event_status': 'disabled'}
        try:
            with self._lock:
                if self._producer is None:
                    if self.producer_factory is None:
                        from kafka import KafkaProducer
                        factory = KafkaProducer
                    else:
                        factory = self.producer_factory
                    self._producer = factory(bootstrap_servers=self.config.bootstrap_servers.split(','),
                                             acks='all', retries=1, max_block_ms=1500,
                                             request_timeout_ms=3000, bootstrap_timeout_ms=1500)
                # A server UUID is not evidence of delivery: wait for actual broker ACK.
                self._producer.send(self.config.topic, key=event['account_id'].encode(),
                                    value=json.dumps(event, separators=(',', ':'), allow_nan=False).encode()).get(timeout=5)
            return {'event_status': 'published', 'event_id': event['event_id']}
        except Exception as exc:
            log.warning('Kafka publication failed event_id=%s type=%s; business transaction already committed',
                        event['event_id'], type(exc).__name__)
            return {'event_status': 'failed', 'event_id': event['event_id'], 'event_error_code': 'KAFKA_PUBLISH_FAILED'}

    def close(self):
        with self._lock:
            if self._producer is not None:
                try:
                    self._producer.close(timeout=2)
                except Exception:
                    log.warning('Kafka producer close failed')
                self._producer = None
