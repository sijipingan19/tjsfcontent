"""Kafka-only consumer state, one writer process; durable atomic account snapshots."""
from contextlib import contextmanager
from datetime import timedelta
import logging
import json
import os
from pathlib import Path
import tempfile

from .events import parse_time, utc_now, valid_id, validate_event


def atomic_json(path, value):
    path = Path(path)
    # Deployment explicitly creates the isolated runtime directory and its permissions.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
        temporary = stream.name
        try:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            os.unlink(temporary)
            raise
    try:
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class StateStore:
    def __init__(self, runtime):
        self.runtime = Path(runtime)

    def _path(self, account):
        if not isinstance(account, str) or not account.startswith('app:') or not valid_id(account[4:]):
            raise ValueError('Expected canonical app account')
        return self.runtime / (account[4:] + '.json')

    def load(self, account):
        path = self._path(account)
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'schema_version': 1, 'subject_key': account, 'last_event_id': None,
                    'last_event_at': None, 'signals': {}, 'seen_event_ids': [], 'recent_events': []}
        if state.get('schema_version') != 1 or state.get('subject_key') != account or not all(
                isinstance(state.get(k), t) for k, t in [('signals', dict), ('seen_event_ids', list), ('recent_events', list)]):
            raise ValueError('Invalid event state')
        return state

    def apply(self, event, genres):
        validate_event(event)
        if not isinstance(genres, list) or not all(isinstance(g, str) for g in genres):
            raise ValueError('Expected real movie genres')
        state = self.load(event['account_id'])
        if event['event_id'] in state['seen_event_ids']:
            return 'duplicate'
        family = 'rating' if event['action'] == 'rating' else 'favorite'
        key = family + ':' + event['movie_id']
        existing = state['signals'].get(key)
        order = (parse_time(event['occurred_at']), event['event_id'])
        fresh = existing is None or order > (parse_time(existing['occurred_at']), existing['event_id'])
        outcome = 'applied' if fresh else 'stale'
        if fresh:
            state['signals'][key] = dict(event, genres=genres)
        # Retain per-key removal tombstones to prevent late adds reviving a deleted favorite.
        if state['last_event_at'] is None or order > (parse_time(state['last_event_at']), state['last_event_id']):
            state['last_event_id'], state['last_event_at'] = event['event_id'], event['occurred_at']
        state['seen_event_ids'] = (state['seen_event_ids'] + [event['event_id']])[-10000:]
        state['recent_events'] = (state['recent_events'] + [dict(event, outcome=outcome)])[-50:]
        state['updated_at'] = utc_now().isoformat()
        atomic_json(self._path(event['account_id']), state)
        return outcome


def process_message(value, key, store, lookup):
    """Reject poison messages; propagate infrastructure errors (never commit them)."""
    try:
        if not isinstance(value, bytes) or len(value) > 16384:
            return 'rejected'
        event = validate_event(json.loads(value.decode('utf-8')))
        if key != event['account_id'].encode():
            return 'rejected'
    except (ValueError, TypeError, UnicodeDecodeError):
        return 'rejected'
    genres = lookup(int(event['account_id'][4:]), int(event['movie_id']))
    if genres is None:
        return 'rejected'
    return store.apply(event, genres)


def lookup_genres(database, account_id, movie_id):
    from sqlalchemy import text
    with database.connection() as conn:
        account = conn.execute(text("SELECT account_id FROM accounts WHERE account_id=:id AND runtime_mode='business'"),
                               {'id': account_id}).first()
        if account is None:
            return None
        row = conn.execute(text('SELECT genres FROM movies WHERE movie_id=:id'), {'id': movie_id}).first()
        if row is None:
            return None
        return [genre for genre in (row[0] or '').split('|') if genre and genre != '(no genres listed)']


def poll_once(consumer, store, lookup):
    from kafka.structs import TopicPartition, OffsetAndMetadata
    counts = {'applied': 0, 'duplicate': 0, 'stale': 0, 'rejected': 0}
    batches = consumer.poll(timeout_ms=1000, max_records=50)
    for records in batches.values():
        for record in records:
            outcome = process_message(record.value, record.key, store, lookup)
            # Never use commit() with no offsets: poll prefetches later messages.
            partition = TopicPartition(record.topic, record.partition)
            consumer.commit({partition: OffsetAndMetadata(record.offset + 1, '')})
            counts[outcome] += 1
    return counts


def short_term(state, *, now=None, catalog=None):
    now = now or utc_now()
    profile = {}
    for event in state['signals'].values():
        age = max(0.0, (now - parse_time(event['occurred_at'])).total_seconds())
        if age > timedelta(hours=24).total_seconds() or event['action'] == 'favorite_remove':
            continue
        weight = (event['rating'] - 3) / 2 if event['action'] == 'rating' else 1.5
        genres = event['genres'] if catalog is None else catalog.get(event['movie_id'], [])
        genres = [g for g in genres if g and g != '(no genres listed)']
        if not genres:
            continue
        decay = 0.5 ** (age / timedelta(hours=6).total_seconds())
        for genre in genres:
            profile[genre] = profile.get(genre, 0.0) + weight * decay / len(genres)
    return {genre: weight for genre, weight in sorted(profile.items()) if abs(weight) > 1e-12}


@contextmanager
def single_writer(runtime):
    with (Path(runtime) / '_writer.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_consumer(config, database, *, consumer_factory=None):
    if consumer_factory is None:
        from kafka import KafkaConsumer
        consumer_factory = KafkaConsumer
    counts = {'applied': 0, 'duplicate': 0, 'stale': 0, 'rejected': 0}
    with single_writer(config.runtime):
        client = None
        status = 'failed'
        try:
            client = consumer_factory(config.topic, bootstrap_servers=config.bootstrap_servers.split(','),
                                      group_id=config.group_id, enable_auto_commit=False,
                                      auto_offset_reset='earliest', max_poll_records=50,
                                      bootstrap_timeout_ms=5000)
            while True:
                batch = poll_once(client, StateStore(config.runtime),
                                  lambda account, movie: lookup_genres(database, account, movie))
                for name, count in batch.items():
                    counts[name] += count
                atomic_json(config.runtime / '_consumer.json', dict(counts, status='running', last_poll_at=utc_now().isoformat()))
                if batch['rejected']:
                    logging.getLogger('course_events').warning('Rejected invalid Kafka records count=%s', batch['rejected'])
        except KeyboardInterrupt:
            status = 'stopped'
            return 0
        finally:
            try:
                atomic_json(config.runtime / '_consumer.json', dict(counts, status=status, last_poll_at=utc_now().isoformat()))
            finally:
                if client is not None:
                    client.close(autocommit=False)


def main():
    from .events import EventConfig
    config = EventConfig.from_env()
    if not config.enabled:
        return 0
    database = None
    try:
        from deployment.serve import secret
        from backend.app.config import Settings
        from backend.app.db import Database
        if not os.getenv('APP_DB_PASSWORD'):
            os.environ['APP_DB_PASSWORD'] = secret('app_password')
        if not os.getenv('APP_JWT_SECRET'):
            os.environ['APP_JWT_SECRET'] = secret('jwt_secret')
        settings = Settings(_env_file=os.getenv('APP_ENV_FILE', '/dev/null'))
        database = Database(settings)
        database.validate()
        return run_consumer(config, database)
    except Exception as exc:
        logging.getLogger('course_events').error('Consumer stopped; type=%s (no offsets acknowledged after failed write)', type(exc).__name__)
        return 1
    finally:
        if database is not None:
            database.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
