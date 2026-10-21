"""Student extensions: executable RED/GREEN behavior tests, no broker simulation claims."""
import importlib
import unittest


class RRFTests(unittest.TestCase):
    def test_weighted_rank_fusion_filters_deduplicates_and_breaks_ties_numerically(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.rrf'), 'RRF implementation is missing')
        from recommender.student.rrf import fuse
        result = fuse({'content': ['10', '2', '7', '7', '99'], 'itemcf': ['2', '10', '8']},
                      weights={'content': 1.0, 'itemcf': 1.0}, excluded={'7'},
                      catalog={'2', '7', '8', '10'}, limit=10)
        self.assertEqual([x['movie_id'] for x in result], ['2', '10', '8'])
        self.assertAlmostEqual(result[0]['score'], 1/61 + 1/62)
        self.assertEqual(result[0]['recall_ranks'], {'content': 2, 'itemcf': 1})
        self.assertEqual(result[0]['recall_sources'], ['content', 'itemcf'])
        weighted = fuse({'content': ['10', '2'], 'itemcf': ['2', '10']},
                        weights={'content': 2.0, 'itemcf': 1.0}, excluded=set(),
                        catalog={'2', '10'}, limit=1)
        self.assertEqual(weighted[0]['movie_id'], '10')
        self.assertEqual(weighted[0]['rank'], 1)


class RealtimeRankingTests(unittest.TestCase):
    def test_consumed_interest_changes_real_base_ranks_and_excludes_rated_movies(self):
        from recommender.student import rrf
        self.assertTrue(hasattr(rrf, 'rerank'), 'Realtime reranking is missing')
        from recommender.student.consumer import StateStore, short_term
        from recommender.student.events import make_event
        from tempfile import TemporaryDirectory
        from pathlib import Path
        base = rrf.fuse({'content': ['2', '10', '7'], 'itemcf': ['2', '10', '7']},
                        weights={'content': 1, 'itemcf': 1}, excluded=set(),
                        catalog={'2','10','7'}, limit=10)
        catalog = {'2': ['Drama'], '10': ['Action'], '7': ['Action']}
        cold = rrf.rerank(base, {}, catalog=catalog, excluded=set(), limit=10)
        self.assertEqual([x['movie_id'] for x in cold], ['2', '10', '7'])
        with TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            store.apply(make_event(7, 7, 'rating', 5), ['Action'])
            updated = rrf.rerank(base, short_term(store.load('app:7')), catalog=catalog,
                                 excluded={'7'}, limit=10)
        self.assertEqual([x['movie_id'] for x in updated], ['10', '2'])
        self.assertEqual(updated[0]['base_rank'], 2)
        self.assertGreater(updated[0]['short_term_boost'], 0.049)
        self.assertAlmostEqual(updated[0]['score'], updated[0]['base_score']+updated[0]['short_term_boost'])
        self.assertEqual(updated[0]['recall_sources'], ['content', 'itemcf', 'short_term_genres'])
        self.assertNotIn('short_term_genres', updated[0]['recall_ranks'])


class EventTests(unittest.TestCase):
    def test_installed_kafka_producer_reaches_network_timeout_not_invalid_configuration(self):
        from recommender.student.events import EventConfig, Publisher, make_event
        publisher = Publisher(EventConfig(enabled=True, bootstrap_servers='127.0.0.1:1'))
        with self.assertLogs('course_events', level='WARNING') as logs:
            result = publisher.publish(make_event(7,10,'rating',5))
        publisher.close()
        self.assertEqual(result['event_status'], 'failed')
        self.assertTrue(any('type=KafkaTimeoutError' in line for line in logs.output), logs.output)

    def test_server_event_schema_and_explicit_broker_ack_failure(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.events'), 'Events implementation is missing')
        from recommender.student.events import EventConfig, Publisher, make_event, validate_event
        from unittest.mock import Mock
        event = make_event(7, 10, 'rating', '4.5')
        self.assertEqual(validate_event(event), event)
        self.assertEqual(event['account_id'], 'app:7')
        self.assertEqual(event['rating'], 4.5)
        for changes in [{'account_id': 'ml:7'}, {'account_id': 'app:01'}, {'movie_id': '../1'},
                        {'rating': True}, {'rating': 4.1}, {'event_id': 'not-uuid'},
                        {'action': 'client_custom'}, {'occurred_at': '2026-01-01T00:00:00'}, {'other': 1}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_event(dict(event, **changes))
        disabled = Publisher(EventConfig())
        self.assertEqual(disabled.publish(event), {'event_status': 'disabled'})
        producer = Mock()
        publisher = Publisher(EventConfig(enabled=True), producer_factory=lambda **kw: producer)
        result = publisher.publish(event)
        self.assertEqual(result, {'event_status': 'published', 'event_id': event['event_id']})
        self.assertEqual(producer.send.call_args.kwargs['key'], b'app:7')
        producer.send.return_value.get.assert_called_once_with(timeout=5)
        producer.send.return_value.get.side_effect = TimeoutError('broker timeout')
        result = publisher.publish(make_event(7, 10, 'favorite_add'))
        self.assertEqual(result['event_status'], 'failed')
        self.assertEqual(result['event_error_code'], 'KAFKA_PUBLISH_FAILED')


class ExtensionRouteTests(unittest.TestCase):
    def test_rrf_routes_use_business_sources_and_current_actor_only(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.app.extensions'), 'Extension routes are missing')
        from contextlib import contextmanager
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import StaticPool
        from backend.app.main import create_app
        from backend.app.config import Settings
        from backend.app.security import Actor, current_actor
        from recommender.contracts import RecommendationResult, RecommendationItem
        from recommender.data import DATASET, SPLIT
        from recommender.student.events import utc_now
        from fastapi.testclient import TestClient
        import secrets
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        class Database:
            def __init__(self): self.engine = engine
            def check_configuration(self): return None
            def validate(self): return {'status':'ready'}
            @contextmanager
            def connection(self, **kwargs):
                with engine.connect() as conn: yield conn
        with engine.begin() as conn:
            for ddl in ['CREATE TABLE movies(movie_id INTEGER PRIMARY KEY, genres TEXT)',
                        'CREATE TABLE history_users(ml_user_id INTEGER PRIMARY KEY)',
                        'CREATE TABLE accounts(account_id INTEGER PRIMARY KEY)',
                        'CREATE TABLE account_ratings(account_id INTEGER,movie_id INTEGER,rating REAL)',
                        'CREATE TABLE history_ratings(ml_user_id INTEGER,movie_id INTEGER,rating REAL)',
                        'CREATE TABLE account_favorites(account_id INTEGER,movie_id INTEGER)']:
                conn.execute(text(ddl))
            conn.execute(text("INSERT INTO movies VALUES(2,'Drama'),(10,'Action'),(7,'Action')"))
            conn.execute(text('INSERT INTO accounts VALUES(7),(8)'))
            conn.execute(text('INSERT INTO history_users VALUES(1)'))
            conn.execute(text('INSERT INTO account_ratings VALUES(7,7,5)'))
            conn.execute(text('INSERT INTO history_ratings VALUES(1,7,5)'))
        settings = Settings(_env_file=None, env='development', origin='http://testserver', cookie_secure=False,
                            jwt_secret=secrets.token_urlsafe(32), db_name='rec_course_unit_business',
                            db_user='unit_only', db_password=secrets.token_urlsafe(32))
        calls = []
        def recommend(algorithm, request):
            calls.append((algorithm.value, request))
            ids = ['2','10','7'] if algorithm.value == 'content' else ['10','2','7']
            return RecommendationResult(algorithm=algorithm, model_version='unit-'+algorithm.value,
                dataset_version=DATASET, split_version=SPLIT, runtime_mode='business', generated_at=utc_now(),
                items=[RecommendationItem(movie_id=mid, rank=n, score=99-n, algorithm=algorithm,
                                           model_version='unit-'+algorithm.value) for n,mid in enumerate(ids,1)])
        with TemporaryDirectory() as directory, patch('backend.app.main.Database', return_value=Database()), \
             patch.dict('os.environ', {'COURSE_EVENTS_ENABLED':'true', 'COURSE_EVENTS_RUNTIME':directory}), \
             patch('backend.app.extensions.contracts.recommend', side_effect=recommend):
            app = create_app(settings)
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/v1/me/extensions/rrf').status_code, 401)
                app.dependency_overrides[current_actor] = lambda: Actor(7, 'unit-only', 0)
                rrf = client.get('/api/v1/me/extensions/rrf').json()
                self.assertEqual(rrf['subject_key'], 'app:7')
                self.assertEqual([x['movie_id'] for x in rrf['items']], ['2','10'])
                self.assertEqual(rrf['items'][0]['recall_ranks'], {'content':1,'itemcf':2})
                self.assertTrue(all(req.runtime_mode=='business' and req.subject_id==7 for _,req in calls))
                self.assertEqual(client.get('/api/v1/me/extensions/rrf?account_id=8').status_code, 422)
                self.assertEqual(client.get('/api/v1/me/extensions/rrf?limit=51').status_code, 422)
                self.assertEqual(client.get('/api/v1/demo/history-users/999/extensions/rrf').status_code, 404)
                ml = client.get('/api/v1/demo/history-users/1/extensions/rrf').json()
                self.assertEqual(ml['subject_key'], 'ml:1')
                self.assertEqual(calls[-1][1].subject_kind, 'ml')
                self.assertEqual(client.get('/api/v1/demo/history-users/01/extensions/rrf').status_code, 422)
                cold_response = client.get('/api/v1/me/extensions/realtime')
                self.assertEqual(cold_response.status_code, 200, 'Realtime extension route is missing')
                cold = cold_response.json()
                self.assertEqual(cold['strategy'], 'static_fallback_no_events')
                self.assertIsNone(cold['last_event_id'])
                self.assertEqual(cold['short_term'], {})
                from recommender.student.consumer import StateStore, atomic_json
                from recommender.student.events import make_event
                from pathlib import Path
                store = StateStore(Path(directory))
                event = make_event(7, 7, 'rating', 5)
                store.apply(event, ['Action'])
                atomic_json(Path(directory)/'_consumer.json', {'status':'running', 'last_poll_at':utc_now().isoformat()})
                changed = client.get('/api/v1/me/extensions/realtime').json()
                self.assertEqual(changed['strategy'], 'event_driven_genre_rerank')
                self.assertEqual(changed['last_event_id'], event['event_id'])
                self.assertEqual(changed['items'][0]['movie_id'], '10')
                self.assertEqual(changed['base_movie_ids'], ['2','10'])
                self.assertTrue(changed['ranking_changed'])
                events = client.get('/api/v1/me/extensions/events').json()
                self.assertEqual(events['last_event_id'], event['event_id'])
                self.assertEqual(events['recent_events'][0]['event_id'], event['event_id'])
                self.assertEqual(client.get('/api/v1/me/extensions/events?account_id=8').status_code, 422)
                app.dependency_overrides[current_actor] = lambda: Actor(8, 'unit-only-2', 0)
                other = client.get('/api/v1/me/extensions/events').json()
                self.assertIsNone(other['last_event_id'])
                self.assertEqual(other['recent_events'], [])
                self.assertEqual(other['subject_key'], 'app:8')
                app.dependency_overrides.clear()
                self.assertEqual(client.get('/api/v1/me/extensions/events').status_code, 401)


class ApplicationLifecycleTests(unittest.TestCase):
    def test_default_app_initializes_disabled_publisher_and_shuts_it_down(self):
        from backend.app.main import create_app
        from backend.app.config import Settings
        from fastapi.testclient import TestClient
        from unittest.mock import Mock, patch
        import secrets
        settings = Settings(_env_file=None, env='development', origin='http://testserver', cookie_secure=False,
                            jwt_secret=secrets.token_urlsafe(32), db_name='rec_course_unit_business',
                            db_user='unit_only', db_password=secrets.token_urlsafe(32))
        database = Mock()
        with patch('backend.app.main.Database', return_value=database), patch.dict('os.environ', {'COURSE_EVENTS_ENABLED': 'false'}):
            app = create_app(settings)
        self.assertTrue(hasattr(app.state, 'event_publisher'), 'App has no event publisher')
        self.assertFalse(app.state.event_publisher.config.enabled)
        with patch.object(app.state.event_publisher, 'close') as close:
            with TestClient(app) as client:
                self.assertEqual(client.get('/health/live').status_code, 200)
                self.assertEqual(client.put('/api/v1/me/ratings/10', json={'rating': 5}).status_code, 403)
            close.assert_called_once()
        database.engine.dispose.assert_called_once()


class ActivityIntegrationTests(unittest.TestCase):
    def test_rating_and_favorites_publish_after_commit_with_actor_identity(self):
        from backend.app import activity
        from backend.app.security import Actor
        from backend.app.schemas import RatingInput
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from datetime import datetime
        order = []
        conn = Mock()
        conn.execute.return_value.mappings.return_value.one.return_value = {'movie_id': 10, 'rating': 5}
        conn.execute.return_value.scalar_one.return_value = datetime(2026, 1, 1)
        publisher = Mock()
        publisher.publish.side_effect = lambda event: order.append(('publish', event)) or {'event_status': 'published', 'event_id': event['event_id']}
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(event_publisher=publisher)))
        @contextmanager
        def transaction(request, actor):
            order.append(('begin', None))
            yield conn
            order.append(('commit', None))
        actor = Actor(7, 'test-only', 0)
        with patch.object(activity, 'authenticated_write', transaction), patch.object(activity, 'require_movie'):
            rated = activity.rate_movie('10', RatingInput(rating=5), request, actor)
            self.assertEqual(rated.get('event_status'), 'published', 'Committed rating does not publish an event')
            self.assertEqual([x[0] for x in order], ['begin','commit','publish'])
            self.assertEqual(order[-1][1]['account_id'], 'app:7')
            self.assertEqual(order[-1][1]['rating'], 5)
            order.clear()
            saved = activity.favorite('10', request, actor)
            self.assertEqual(saved['event_status'], 'published')
            self.assertEqual(order[-1][1]['action'], 'favorite_add')
            self.assertEqual([x[0] for x in order], ['begin','commit','publish'])
            order.clear()
            removed = activity.unfavorite('10', request, actor)
            self.assertEqual(removed.status_code, 204)
            self.assertEqual(removed.headers['x-event-status'], 'published')
            self.assertEqual(order[-1][1]['action'], 'favorite_remove')
            self.assertEqual([x[0] for x in order], ['begin','commit','publish'])
        @contextmanager
        def rollback(request, actor):
            yield conn
            raise RuntimeError('commit failed')
        publisher.reset_mock()
        with patch.object(activity, 'authenticated_write', rollback), patch.object(activity, 'require_movie'):
            with self.assertRaises(RuntimeError):
                activity.rate_movie('10', RatingInput(rating=5), request, actor)
        publisher.publish.assert_not_called()


class ConsumerServiceTests(unittest.TestCase):
    def test_installed_kafka_consumer_reaches_network_timeout_not_invalid_configuration(self):
        from recommender.student.consumer import run_consumer
        from recommender.student.events import EventConfig
        from kafka.errors import KafkaTimeoutError
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import Mock
        with TemporaryDirectory() as directory:
            config = EventConfig(enabled=True, bootstrap_servers='127.0.0.1:1', runtime=Path(directory))
            with self.assertRaises(KafkaTimeoutError):
                run_consumer(config, Mock())

    def test_executable_consumer_uses_manual_commits_and_closes_cleanly(self):
        from recommender.student import consumer
        self.assertTrue(hasattr(consumer, 'run_consumer'), 'Consumer process entry is missing')
        from recommender.student.events import EventConfig
        from unittest.mock import Mock, patch
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        with TemporaryDirectory() as directory:
            config = EventConfig(enabled=True, runtime=Path(directory))
            broker = Mock()
            broker.poll.side_effect = [{}, KeyboardInterrupt()]
            factory = Mock(return_value=broker)
            self.assertEqual(consumer.run_consumer(config, Mock(), consumer_factory=factory), 0)
            self.assertFalse(factory.call_args.kwargs['enable_auto_commit'])
            self.assertEqual(factory.call_args.kwargs['auto_offset_reset'], 'earliest')
            broker.close.assert_called_once()
            heartbeat = json.loads((Path(directory)/'_consumer.json').read_text())
            self.assertEqual(heartbeat['status'], 'stopped')
            with consumer.single_writer(Path(directory)):
                with self.assertRaises(OSError):
                    with consumer.single_writer(Path(directory)):
                        pass
            broker = Mock()
            broker.poll.side_effect = RuntimeError('broker failed')
            with self.assertRaises(RuntimeError):
                consumer.run_consumer(config, Mock(), consumer_factory=lambda *args, **kw: broker)
            self.assertEqual(json.loads((Path(directory)/'_consumer.json').read_text())['status'], 'failed')
            broker.close.assert_called_once()
        with patch.dict('os.environ', {'COURSE_EVENTS_ENABLED':'false'}):
            self.assertEqual(consumer.main(), 0)

    def test_real_business_lookup_rejects_unknown_account_or_movie(self):
        from recommender.student import consumer
        self.assertTrue(hasattr(consumer, 'lookup_genres'), 'Business genre lookup is missing')
        from sqlalchemy import create_engine, text
        from contextlib import contextmanager
        engine = create_engine('sqlite://')
        class Database:
            @contextmanager
            def connection(self):
                with engine.connect() as conn: yield conn
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE accounts(account_id INTEGER, runtime_mode TEXT)'))
            conn.execute(text('CREATE TABLE movies(movie_id INTEGER, genres TEXT)'))
            conn.execute(text("INSERT INTO accounts VALUES(7,'business'),(8,'evaluation')"))
            conn.execute(text("INSERT INTO movies VALUES(10,'Action|Comedy')"))
        self.assertEqual(consumer.lookup_genres(Database(), 7,10), ['Action','Comedy'])
        self.assertIsNone(consumer.lookup_genres(Database(), 8,10))
        self.assertIsNone(consumer.lookup_genres(Database(), 7,99))
        engine.dispose()


class ConsumerLoopTests(unittest.TestCase):
    def test_poll_commits_only_exact_durable_offsets_and_never_commits_disk_failure(self):
        from recommender.student import consumer
        self.assertTrue(hasattr(consumer, 'poll_once'), 'Executable Kafka poll loop is missing')
        from recommender.student.events import EventConfig, make_event
        from kafka.structs import TopicPartition
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        with TemporaryDirectory() as directory:
            config = EventConfig(enabled=True, runtime=Path(directory))
            store = consumer.StateStore(config.runtime)
            event = make_event(7, 10, 'favorite_add')
            record = SimpleNamespace(topic=config.topic, partition=0, offset=4, key=b'app:7', value=json.dumps(event).encode())
            poison = SimpleNamespace(topic=config.topic, partition=0, offset=5, key=b'app:7', value=b'bad-json')
            broker = Mock()
            broker.poll.return_value = {TopicPartition(config.topic, 0): [record, poison]}
            commits = []
            def commit(offsets):
                self.assertEqual(store.load('app:7')['last_event_id'], event['event_id'])
                commits.append(next(iter(offsets.values())).offset)
            broker.commit.side_effect = commit
            counts = consumer.poll_once(broker, store, lambda a,m: ['Action'])
            self.assertEqual(commits, [5,6])
            self.assertEqual(counts, {'applied':1, 'duplicate':0, 'stale':0, 'rejected':1})
            record.value = json.dumps(make_event(7,10,'favorite_remove')).encode()
            broker.commit.reset_mock()
            with patch.object(consumer, 'atomic_json', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    consumer.poll_once(broker, store, lambda a,m: ['Action'])
            broker.commit.assert_not_called()


class ConsumerTransportTests(unittest.TestCase):
    def test_message_processing_validates_identity_and_ack_waits_for_durable_state(self):
        from recommender.student import consumer
        self.assertTrue(hasattr(consumer, 'process_message'), 'Kafka record processing is missing')
        from recommender.student.events import make_event
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        from unittest.mock import patch
        event = make_event(7, 10, 'rating', 5)
        with TemporaryDirectory() as directory:
            store = consumer.StateStore(Path(directory))
            lookup_calls = []
            def lookup(account, movie):
                lookup_calls.append((account, movie))
                return ['Action'] if (account, movie) == (7, 10) else None
            self.assertEqual(consumer.process_message(json.dumps(event).encode(), b'app:7', store, lookup), 'applied')
            self.assertEqual(store.load('app:7')['last_event_id'], event['event_id'])
            self.assertEqual(consumer.process_message(json.dumps(event).encode(), b'app:8', store, lookup), 'rejected')
            self.assertEqual(len(lookup_calls), 1)
            invalid = dict(event, account_id='ml:7')
            self.assertEqual(consumer.process_message(json.dumps(invalid).encode(), b'ml:7', store, lookup), 'rejected')
            unknown = make_event(8, 10, 'favorite_add')
            self.assertEqual(consumer.process_message(json.dumps(unknown).encode(), b'app:8', store, lookup), 'rejected')
            with patch.object(consumer, 'atomic_json', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    consumer.process_message(json.dumps(make_event(7, 10, 'favorite_add')).encode(), b'app:7', store, lookup)
            self.assertEqual(consumer.process_message(b'not json', b'app:7', store, lookup), 'rejected')


class ConsumerStateTests(unittest.TestCase):
    def test_atomic_consumer_state_deduplicates_and_reverses_removed_favorites(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.consumer'), 'Consumer implementation is missing')
        from recommender.student.consumer import StateStore, short_term
        from recommender.student.events import make_event, utc_now
        from tempfile import TemporaryDirectory
        from datetime import timedelta
        from pathlib import Path
        import json
        with TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            event = make_event(7, 10, 'favorite_add')
            self.assertEqual(store.apply(event, ['Action']), 'applied')
            self.assertEqual(store.apply(event, ['Action']), 'duplicate')
            snapshot = store.load('app:7')
            self.assertEqual(snapshot['last_event_id'], event['event_id'])
            self.assertGreater(short_term(snapshot, now=utc_now())['Action'], 1.49)
            removal = make_event(7, 10, 'favorite_remove')
            self.assertEqual(store.apply(removal, ['Action']), 'applied')
            self.assertEqual(short_term(store.load('app:7')), {})
            old = make_event(7, 10, 'favorite_add')
            old['occurred_at'] = (utc_now() - timedelta(hours=1)).isoformat()
            self.assertEqual(store.apply(old, ['Action']), 'stale')
            self.assertEqual(short_term(store.load('app:7')), {})
            rating = make_event(7, 2, 'rating', 5)
            self.assertEqual(store.apply(rating, ['Drama']), 'applied')
            persisted = json.loads((Path(directory) / '7.json').read_text())
            self.assertEqual(persisted['last_event_id'], rating['event_id'])
            self.assertEqual(len(persisted['recent_events']), 4)
            self.assertGreater(short_term(persisted)['Drama'], .99)
            self.assertEqual(short_term(persisted, now=utc_now()+timedelta(hours=25)), {})
            self.assertEqual(store.load('app:8')['recent_events'], [])
            with self.assertRaises(ValueError):
                store.load('app:../7')
            (Path(directory)/'8.json').write_text('{corrupted')
            with self.assertRaises(ValueError):
                store.load('app:8')


if __name__ == '__main__':
    unittest.main()
