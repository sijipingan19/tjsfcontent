"""明确仅用于单元测试的合成数据；正式训练/评价不使用这些数据。"""
import importlib.util
import unittest


class CoreTests(unittest.TestCase):
    def test_popular_bayesian_support_and_numeric_ties(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.core'), '尚未实现真实推荐核心')
        from recommender.student.core import fit_popular, rank_scores
        ids = ['1', '2', '10']
        rows = ([('u1', '1', 5.0)] + [(f'u{i}', '2', 4.5) for i in range(10)]
                + [(f'background{i}', '10', 3.0) for i in range(100)])
        scores, counts = fit_popular(ids, rows, prior=10.0)
        self.assertGreater(scores[1], scores[0])
        self.assertEqual(counts.tolist(), [1, 10, 100])
        self.assertEqual(rank_scores(['10', '2', '1'], [1, 1, 1], {'1'}, 10), [('2', 1.0), ('10', 1.0)])

    def test_content_genres_cosine_uses_positive_history(self):
        from recommender.student import core
        self.assertTrue(hasattr(core, 'fit_genres'), '缺少真实genre表示')
        ids = ['1', '2', '3', '10']
        features, genres = core.fit_genres(ids, {'1': 'Action', '2': 'Drama', '3': 'Action', '10': 'Drama'})
        self.assertEqual(set(genres), {'Action', 'Drama'})
        action = core.content_scores(ids, features, {'1': 5.0, '2': 1.0})
        drama = core.content_scores(ids, features, {'2': 4.0})
        self.assertAlmostEqual(action[2], 1.0)
        self.assertEqual(action[3], 0.0)
        self.assertAlmostEqual(drama[3], 1.0)
        self.assertIsNone(core.content_scores(ids, features, {'1': 3.5}))

    def test_itemcf_binary_positive_cosine_shrinkage_and_neighbors(self):
        from recommender.student import core
        self.assertTrue(hasattr(core, 'fit_itemcf'), '缺少真实正反馈共现拟合')
        import numpy as np
        ids = ['1', '2', '3', '10']
        rows = [('1', '1', 5), ('1', '1', 5), ('1', '2', 4), ('1', '3', 2),
                ('2', '1', 4), ('2', '2', 4), ('2', '10', 5), ('3', '3', 5)]
        neighbors = core.fit_itemcf(ids, rows, top_k=1, shrinkage=2.0)
        self.assertEqual(neighbors.nnz, 3)
        self.assertAlmostEqual(neighbors[0, 1], 0.5)
        self.assertEqual(neighbors.diagonal().sum(), 0)
        a = core.itemcf_scores(ids, neighbors, {'1': 5})
        b = core.itemcf_scores(ids, neighbors, {'2': 5})
        self.assertGreater(a[1], a[0])
        self.assertGreater(b[0], b[1])
        self.assertIsNone(core.itemcf_scores(ids, neighbors, {'1': 1}))
        self.assertTrue(np.isfinite(neighbors.data).all())

    def test_provider_lazy_loading_exclusions_and_feedback_changes(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.providers'), '尚未实现Provider')
        from recommender.student.providers import StudentProvider
        from recommender.contracts import RecommendationRequest
        from recommender.data import DATASET, SPLIT
        from unittest.mock import patch
        import tempfile
        from pathlib import Path
        import numpy as np
        from scipy import sparse
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(StudentProvider('content', root / 'missing.json')._model)
            ids = np.asarray(['1', '2', '3', '10'])
            features = np.asarray([[1, 0], [0, 1], [1, 0], [0, 1]], dtype=float)
            neighbors = sparse.csr_matrix(np.asarray([[0, 0, .5, 0], [0, 0, 0, .5], [.5, 0, 0, 0], [0, .5, 0, 0]]))
            for algorithm in ['popular', 'content', 'itemcf', 'cold_start']:
                manifest = self.fixture_model(root, algorithm, movie_ids=ids, popular_scores=np.asarray([4, 3, 2, 1.]),
                    genre_features=features, neighbor_data=neighbors.data, neighbor_indices=neighbors.indices,
                    neighbor_indptr=neighbors.indptr)
                provider = StudentProvider(algorithm, manifest)
                request = RecommendationRequest('app', 1, 3, 'business', DATASET, SPLIT)
                with patch('recommender.business.read_history', side_effect=AssertionError('离线接口不能读取数据库')):
                    empty = provider.recommend_history(request, {})
                    action = provider.recommend_history(request, {'1': 5, '2': 1}, excluded={'10'})
                    drama = provider.recommend_history(request, {'2': 5})
                self.assertEqual([i.movie_id for i in action.items], ['3'])
                self.assertTrue(all(i.recall_sources for i in empty.items))
                if algorithm != 'popular':
                    self.assertIn('popular_fallback:no_positive_profile', empty.items[0].recall_sources)
                    self.assertEqual(action.items[0].movie_id, '3')
                    self.assertEqual(drama.items[0].movie_id, '10')

    def test_als_real_factors_ml_identity_and_app_ridge_fold_in(self):
        from recommender.student.providers import StudentProvider
        self.assertTrue(hasattr(StudentProvider, '_als_scores'), '缺少ALS真实因子推断')
        from recommender.contracts import RecommendationRequest
        from recommender.data import DATASET, SPLIT, ContractError
        from pathlib import Path
        import tempfile
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = self.fixture_model(Path(tmp), 'als', movie_ids=np.asarray(['1', '2', '3', '10']),
                item_factors=np.asarray([[1., 0], [0, 1], [2, 0], [0, 2]]),
                user_ids=np.asarray(['1']), user_factors=np.asarray([[0., 1]]), popular_scores=np.asarray([4., 3, 2, 1]))
            provider = StudentProvider('als', path)
            ml = RecommendationRequest('ml', 1, 1, 'business', DATASET, SPLIT)
            app = RecommendationRequest('app', 1, 1, 'business', DATASET, SPLIT)
            self.assertEqual(provider.recommend_history(ml, {'1': 5}).items[0].movie_id, '10')
            result = provider.recommend_history(app, {'1': 5})
            self.assertEqual(result.items[0].movie_id, '3')
            self.assertAlmostEqual(result.items[0].score, 10 / 1.1)
            self.assertIn('als:ridge_fold_in', result.items[0].recall_sources)
            self.assertIn('popular_fallback:no_positive_profile', provider.recommend_history(app, {}).items[0].recall_sources)
            with self.assertRaises(ContractError) as caught:
                StudentProvider('als', Path(tmp) / 'missing.json').recommend_history(app, {})
            self.assertEqual(caught.exception.code, 'MODEL_BINDING_INVALID')

    def test_training_evaluation_reads_only_training_and_saves_four_bound_models(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.train'), '尚未实现训练命令')
        from recommender.student.train import train_mode
        from recommender.data import FrozenData, DATASET
        from recommender.validation import check_model
        from pathlib import Path
        import tempfile
        import numpy as np
        real = FrozenData()
        class UnitData:
            """仅合成训练单元夹具；不用于任何实验工件。"""
            package = real.package
            manifest = real.manifest
            calls = []
            def catalog(self):
                return {'1', '2', '3', '10'}
            def training(self):
                self.calls.append('training')
                return iter([{'userId': '1', 'movieId': '1', 'rating': '5'},
                    {'userId': '1', 'movieId': '3', 'rating': '4'},
                    {'userId': '2', 'movieId': '2', 'rating': '5'},
                    {'userId': '2', 'movieId': '10', 'rating': '4'}])
            def rows(self, name):
                self.calls.append(name)
                if name != 'movies.csv':
                    raise AssertionError('拟合不可读取留出分区或业务文件')
                return iter([{'movie_id': '1', 'genres': 'Action'}, {'movie_id': '2', 'genres': 'Drama'},
                    {'movie_id': '3', 'genres': 'Action'}, {'movie_id': '10', 'genres': 'Drama'}])
        with tempfile.TemporaryDirectory() as tmp:
            data = UnitData()
            paths = train_mode('evaluation', Path(tmp), data=data)
            self.assertEqual(set(paths), {'popular', 'content', 'itemcf', 'cold_start'})
            self.assertEqual(data.calls.count('training'), 1)
            self.assertEqual(data.calls, ['training', 'movies.csv'])
            for algorithm, path in paths.items():
                model = check_model(path, 'evaluation', algorithm, real)
                self.assertEqual(model.dataset_version, DATASET)
                self.assertEqual(set(model.training_files), {'evaluation/train.csv'})
                with np.load(path.parent / 'model.npz', allow_pickle=False) as arrays:
                    self.assertEqual(arrays['movie_ids'].tolist(), ['1', '2', '3', '10'])

    def test_metrics_keep_empty_users_fixed_ten_and_catalog_coverage(self):
        self.assertIsNotNone(importlib.util.find_spec('recommender.student.evaluate'), '尚未实现真实离线指标')
        from recommender.student.evaluate import compute_metrics
        predictions = {'1': ['1', '2'], '2': []}
        relevance = {'1': {'1', '3'}, '2': {'2'}}
        metrics = compute_metrics(predictions, relevance, {'1', '2', '3', '10'})
        self.assertAlmostEqual(metrics['precision_at_10'], 0.05)
        self.assertAlmostEqual(metrics['recall_at_10'], 0.25)
        self.assertAlmostEqual(metrics['coverage_at_10'], 0.5)
        self.assertAlmostEqual(metrics['user_coverage_at_10'], 0.5)
        self.assertEqual(metrics['user_count'], 2)
        self.assertEqual(metrics['empty_users'], 1)
        with self.assertRaises(ValueError):
            compute_metrics({'1': ['1']}, relevance, {'1', '2', '3', '10'})

    def test_test_partition_excludes_validation_without_using_it_for_profile(self):
        from recommender.student import evaluate
        self.assertTrue(hasattr(evaluate, 'partition_inputs'), '尚未实现隔离评价数据准备')
        class UnitData:
            def training(self):
                return iter([{'userId': '1', 'movieId': '1', 'rating': '5'}])
            def rows(self, name, *, evaluation=False):
                assert evaluation
                rows = {'evaluation/validation.csv': [
                    {'userId': '1', 'movieId': '2', 'rating': '5'}], 'evaluation/test.csv': [
                    {'userId': '1', 'movieId': '2', 'rating': '5'},
                    {'userId': '1', 'movieId': '3', 'rating': '4'},
                    {'userId': '1', 'movieId': '10', 'rating': '3.5'}]}
                return iter(rows[name])
        history, excluded, relevant = evaluate.partition_inputs(UnitData(), 'test', {'1'}, {'1', '2', '3', '10'})
        self.assertEqual(history, {'1': {'1': 5.0}})
        self.assertEqual(excluded, {'1': {'1', '2'}})
        self.assertEqual(relevant, {'1': {'3'}})

    def test_test_requires_explicit_frozen_hyperparameters(self):
        from recommender.student import evaluate
        self.assertTrue(hasattr(evaluate, 'freeze_models'), '尚未实现测试前参数冻结')
        from pathlib import Path
        import tempfile
        import json
        import numpy as np
        from recommender.data import ContractError
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.fixture_model(root / 'models' / 'evaluation', 'popular',
                movie_ids=np.asarray(['1', '2']), popular_scores=np.asarray([4., 3.]))
            output = root / 'artifacts'
            with self.assertRaises(ContractError):
                evaluate.require_frozen(root / 'models', output, ['popular'])
            evaluate.freeze_models(root / 'models', output, ['popular'])
            evaluate.require_frozen(root / 'models', output, ['popular'])
            raw = json.loads(path.read_text())
            raw['parameters']['changed_after_freeze'] = True
            path.write_text(json.dumps(raw), encoding='utf-8')
            with self.assertRaises(ContractError):
                evaluate.require_frozen(root / 'models', output, ['popular'])

    @unittest.skipUnless(__import__('os').environ.get('CINELAB_INTEGRATION') == '1', '显式选择全量真实冻结用户集成测试')
    def test_real_popular_evaluation_exports_checked_predictions_and_public_aggregate(self):
        from recommender.student import evaluate
        self.assertTrue(hasattr(evaluate, 'evaluate_partition'), '尚未实现完整评价导出')
        from pathlib import Path
        import tempfile
        import json
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            public = output / 'public-summary.json'
            with patch('recommender.business.read_history', side_effect=AssertionError('评价不能读业务数据库')):
                rows = evaluate.evaluate_partition('validation', ['popular'], output_root=output,
                    public_summary_path=public)
            self.assertEqual(rows[0]['user_count'], 4209)
            self.assertEqual(rows[0]['algorithm'], 'popular')
            self.assertGreater(rows[0]['precision_at_10'], 0)
            summary = json.loads(public.read_text(encoding='utf-8'))
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(len(summary['items']), 1)
            self.assertNotIn('ml_user_id', public.read_text())
            self.assertEqual(len((output / 'validation/popular-predictions.jsonl').read_text().splitlines()), 4209)

    def test_five_business_providers_are_registered_without_loading_models(self):
        from recommender.contracts import Algorithm, PROVIDERS
        from recommender.student.providers import StudentProvider
        from pathlib import Path
        self.assertEqual(set(PROVIDERS), set(Algorithm))
        for algorithm, provider in PROVIDERS.items():
            self.assertIsInstance(provider, StudentProvider)
            self.assertEqual(provider.model_manifest_path, Path('models/business') / (algorithm.value + '-v1') / 'manifest.json')
            self.assertIsNone(provider._model)

    @staticmethod
    def fixture_model(root, algorithm, **arrays):
        """仅临时单元工件，不是实验模型或评价成绩。"""
        from recommender.data import DATASET, SPLIT, SCHEMA, MANIFEST_SHA, FrozenData, sha256
        from recommender.validation import ModelManifest
        import numpy as np
        target = root / (algorithm + '-v1')
        target.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(target / 'model.npz', **arrays)
        data = FrozenData()
        model = ModelManifest(algorithm=algorithm, model_version=algorithm + '-unit-test', runtime_mode='business',
            dataset_version=DATASET, split_version=SPLIT, schema_version=SCHEMA, manifest_sha256=MANIFEST_SHA,
            training_files={'business/history_ratings.csv': data.manifest['files']['business/history_ratings.csv']['sha256']},
            artifacts={'model.npz': sha256(target / 'model.npz')}, parameters={'unit_test_only': True}, tool_versions={'fixture': '1'})
        path = target / 'manifest.json'
        path.write_text(model.model_dump_json(), encoding='utf-8')
        return path


if __name__ == '__main__':
    unittest.main()
