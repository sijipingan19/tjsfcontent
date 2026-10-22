"""公开评价摘要的单元测试；所有临时指标仅为测试样例，不是实验成绩。"""
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class EvaluationSummaryTests(unittest.TestCase):
    def test_missing_summary_is_not_a_completed_evaluation(self):
        spec = importlib.util.find_spec('backend.app.evaluation_summary')
        self.assertIsNotNone(spec, '尚未实现安全读取真实评价摘要的入口')
        module = importlib.import_module('backend.app.evaluation_summary')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(module.SummaryNotReady):
                module.load_summary(Path(temp) / 'not-created.json')

    def test_completed_summary_contains_all_four_algorithms_and_fixed_users(self):
        from backend.app.evaluation_summary import load_summary
        from recommender.data import DATASET, SPLIT
        sample = {
            'status': 'completed', 'dataset_version': DATASET, 'split_version': SPLIT,
            'items': [dict(algorithm=a, model_version=a + '-v1', partition='validation',
                           user_count=4209, precision_at_10=0.05, recall_at_10=0.1,
                           coverage_at_10=0.8)
                      for a in ['popular', 'content', 'itemcf', 'als']],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'unit-test-only.json'
            path.write_text(json.dumps(sample), encoding='utf-8')
            self.assertEqual(load_summary(path), sample)

    def test_invalid_or_incomplete_summary_is_rejected(self):
        from backend.app.evaluation_summary import load_summary
        from recommender.data import DATASET, SPLIT
        valid = {'status': 'completed', 'dataset_version': DATASET, 'split_version': SPLIT,
                 'items': [dict(algorithm=a, model_version=a + '-v1', partition='validation',
                                user_count=4209, precision_at_10=0.05, recall_at_10=0.1)
                           for a in ['popular', 'content', 'itemcf', 'als']]}
        cases = []
        def altered(change):
            value = json.loads(json.dumps(valid))
            change(value)
            cases.append(value)
        altered(lambda x: x.update(dataset_version='wrong-version'))
        altered(lambda x: x.update(status='not_run'))
        altered(lambda x: x['items'].pop())
        altered(lambda x: x['items'][0].update(user_count=4208))
        altered(lambda x: x['items'][0].update(precision_at_10=float('nan')))
        altered(lambda x: x['items'][0].update(recall_at_10=1.5))
        altered(lambda x: x['items'][0].update(precision_at_10=True))
        altered(lambda x: x['items'][0].update(partition='cold-k0'))
        altered(lambda x: x['items'][0].update(algorithm='als'))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'invalid-unit-test-only.json'
            for index, value in enumerate(cases):
                with self.subTest(case=index):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_summary(path)

    def test_api_returns_validated_real_summary_file(self):
        from unittest.mock import patch
        from backend.app import experiments
        from recommender.data import DATASET, SPLIT
        sample = {'status': 'completed', 'dataset_version': DATASET, 'split_version': SPLIT,
                  'items': [dict(algorithm=a, model_version=a + '-v1', partition='validation',
                                 user_count=4209, precision_at_10=0.05, recall_at_10=0.1)
                            for a in ['popular', 'content', 'itemcf', 'als']]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'api-unit-test-only.json'
            path.write_text(json.dumps(sample), encoding='utf-8')
            with patch.object(experiments, 'EVALUATION_SUMMARY_PATH', path, create=True):
                self.assertEqual(experiments.evaluations(), sample)


if __name__ == '__main__':
    unittest.main()
