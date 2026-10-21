"""冻结用户、全目录Top10评价；不拟合、不连接数据库、不把标签交给Provider。"""
import json
from pathlib import Path
from datetime import datetime, timezone

from recommender.data import DATASET, SPLIT, require, sha256
from recommender.validation import ModelManifest

ROOT = Path(__file__).resolve().parents[2]
ALGORITHMS = ['popular', 'content', 'itemcf', 'als', 'cold_start']


def configuration_snapshot(models_root, algorithms):
    source_names = ['core.py', 'providers.py', 'train.py', 'evaluate.py']
    result = {'dataset_version': DATASET, 'split_version': SPLIT,
              'source_sha256': {name: sha256(Path(__file__).parent / name) for name in source_names}, 'algorithms': {}}
    for algorithm in algorithms:
        path = Path(models_root) / 'evaluation' / (algorithm + '-v1') / 'manifest.json'
        require(path.is_file(), '缺少真实评价模型：' + algorithm, 'MODEL_BINDING_INVALID')
        model = ModelManifest.model_validate_json(path.read_text(encoding='utf-8'))
        result['algorithms'][algorithm] = {'model_version': model.model_version, 'manifest_sha256': sha256(path),
                                           'parameters': model.parameters, 'artifacts': model.artifacts,
                                           'tool_versions': model.tool_versions}
    return result


def freeze_models(models_root, output_root, algorithms):
    """显式冻结，在任何test标签读取之前完成；已有冻结不可静默覆盖。"""
    current = configuration_snapshot(models_root, algorithms)
    path = Path(output_root) / 'frozen-parameters.json'
    if path.exists():
        require_frozen(models_root, output_root, algorithms)
        return path
    payload = {**current, 'status': 'frozen_before_test', 'frozen_at': datetime.now(timezone.utc).isoformat(),
               'declaration': '仅train拟合；validation用于检查；test尚未用于选参；不将validation并回train。'}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def require_frozen(models_root, output_root, algorithms):
    path = Path(output_root) / 'frozen-parameters.json'
    require(path.is_file(), 'test前须先用--freeze-only显式冻结参数', 'EVALUATION_INPUT_INVALID')
    frozen = json.loads(path.read_text(encoding='utf-8'))
    current = configuration_snapshot(models_root, algorithms)
    require(frozen.get('status') == 'frozen_before_test' and frozen.get('dataset_version') == DATASET and
            frozen.get('split_version') == SPLIT and frozen.get('source_sha256') == current['source_sha256'] and
            all(frozen.get('algorithms', {}).get(a) == item for a, item in current['algorithms'].items()),
            '模型/参数/代码与测试前冻结声明不一致，不能继续test', 'EVALUATION_INPUT_INVALID')
    return frozen


def partition_inputs(data, partition, users, catalog):
    require(partition in {'validation', 'test'}, '此入口只负责warm validation/test', 'EVALUATION_INPUT_INVALID')
    histories = {uid: {} for uid in users}
    excluded = {uid: set() for uid in users}
    for row in data.training():
        uid = row['userId']
        if uid in histories:
            histories[uid][row['movieId']] = float(row['rating'])
            excluded[uid].add(row['movieId'])
    if partition == 'test':
        for row in data.rows('evaluation/validation.csv', evaluation=True):
            if row['userId'] in excluded:
                excluded[row['userId']].add(row['movieId'])
    relevant = {uid: set() for uid in users}
    for row in data.rows(f'evaluation/{partition}.csv', evaluation=True):
        uid, mid = row['userId'], row['movieId']
        if uid in relevant and mid in catalog and mid not in excluded[uid] and float(row['rating']) >= 4.0:
            relevant[uid].add(mid)
    require(all(relevant.values()), '固定用户必须有候选内正反馈目标', 'EVALUATION_INPUT_INVALID')
    return histories, excluded, relevant


def compute_metrics(predictions, relevance, catalog):
    require(set(predictions) == set(relevance) and len(relevance) > 0 and len(catalog) > 0,
            '评价不能漏掉空结果用户', 'EVALUATION_RESULT_INVALID')
    precision, recall, union, nonempty, lengths = [], [], set(), 0, []
    for uid, relevant in relevance.items():
        ids = predictions[uid]
        require(bool(relevant) and len(ids) <= 10 and len(ids) == len(set(ids)) and set(ids) <= catalog,
                '相关目标或Top10候选非法', 'EVALUATION_RESULT_INVALID')
        hits = len(set(ids) & relevant)
        precision.append(hits / 10.0)
        recall.append(hits / len(relevant))
        union.update(ids)
        nonempty += bool(ids)
        lengths.append(len(ids))
    count = len(relevance)
    return {'user_count': count, 'precision_at_10': sum(precision) / count, 'recall_at_10': sum(recall) / count,
            'coverage_at_10': len(union) / len(catalog), 'user_coverage_at_10': nonempty / count,
            'unique_recommended_movies': len(union), 'empty_users': count - nonempty,
            'average_list_length': sum(lengths) / count}


def publish_aggregates(output_root, public_summary_path):
    """公开文件采用白名单字段；绝不复制逐用户预测或评分。"""
    import csv
    fields = ['algorithm', 'partition', 'model_version', 'user_count', 'precision_at_10', 'recall_at_10',
              'coverage_at_10', 'user_coverage_at_10', 'unique_recommended_movies', 'empty_users',
              'average_list_length', 'task_sha256', 'model_manifest_sha256']
    items = []
    for partition in ['validation', 'test']:
        # 公开摘要仅包含四算法（Popular/Content/ItemCF/ALS）；cold_start 属于配套策略，不进入四算法评价表。
        for algorithm in ['popular', 'content', 'itemcf', 'als']:
            path = Path(output_root) / partition / (algorithm + '-metrics.json')
            if path.exists():
                raw = json.loads(path.read_text(encoding='utf-8'))
                require(raw['algorithm'] == algorithm and raw['partition'] == partition,
                        '聚合文件身份不符', 'EVALUATION_RESULT_INVALID')
                items.append({name: raw[name] for name in fields})
    payload = {'status': 'completed', 'dataset_version': DATASET, 'split_version': SPLIT,
               'generated_at': datetime.now(timezone.utc).isoformat(),
               'coverage_definition': 'coverage_at_10 = distinct recommended movies / frozen catalog size 8459; '
                                      'user_coverage_at_10 = users with nonempty recommendations / all fixed eligible users',
               'metrics_definition': 'macro P@10 (denominator always 10), macro R@10 (candidate relevant targets); '
                                     'all fixed users retained, including empty and short lists',
               'items': items}
    target = Path(public_summary_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(target)
    with (Path(output_root) / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(items)
    return payload


def evaluate_partition(partition, algorithms=None, *, models_root=ROOT / 'models',
                       output_root=ROOT / 'artifacts/evaluation',
                       public_summary_path=ROOT / 'models/business/evaluation-summary.json'):
    from recommender.contracts import Algorithm, RecommendationRequest
    from recommender.data import FrozenData, SCHEMA
    from recommender.evaluation import (EvaluationResult, eligible_users, task, check_predictions, check_evaluation_result)
    from recommender.validation import check_model, check_result
    from .providers import StudentProvider
    require(partition in {'validation', 'test'}, '只支持冻结warm validation/test', 'EVALUATION_INPUT_INVALID')
    algorithms = list(ALGORITHMS if algorithms is None else algorithms)
    require(bool(algorithms) and len(algorithms) == len(set(algorithms)) and set(algorithms) <= set(ALGORITHMS),
            '评价算法列表非法', 'EVALUATION_INPUT_INVALID')
    # Guard before constructing any holdout input iterator.
    if partition == 'test':
        require_frozen(models_root, output_root, algorithms)
    data = FrozenData()
    models = {}
    paths = {}
    for algorithm in algorithms:
        path = Path(models_root) / 'evaluation' / (algorithm + '-v1') / 'manifest.json'
        models[algorithm] = check_model(path, 'evaluation', algorithm, data)
        paths[algorithm] = path
    users, catalog = eligible_users(data, partition), data.catalog()
    current_task = task(data, partition)
    histories, excluded, relevance = partition_inputs(data, partition, users, catalog)
    directory = Path(output_root) / partition
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'task.json').write_text(json.dumps(current_task, ensure_ascii=False, indent=2), encoding='utf-8')
    snapshot = configuration_snapshot(models_root, algorithms)
    (directory / 'versions-parameters.json').write_text(json.dumps({**snapshot,
        'partition': partition, 'hyperparameters_frozen': partition == 'test',
        'fit_declaration': 'evaluation/train.csv only; no validation/test/cold data for fitting; no refit on validation',
        'test_policy': 'test requires explicit frozen-parameters.json matching source and manifests'},
        ensure_ascii=False, indent=2), encoding='utf-8')
    rows = []
    for algorithm in algorithms:
        provider = StudentProvider(algorithm, paths[algorithm])
        predictions_path = directory / (algorithm + '-predictions.jsonl')
        predictions = {}
        with predictions_path.open('w', encoding='utf-8', newline='\n') as stream:
            for uid in sorted(users, key=int):
                request = RecommendationRequest('ml', int(uid), 10, 'evaluation', DATASET, SPLIT)
                # Relevant holdout targets never enter the provider or ratings profile.
                result = provider.recommend_history(request, histories[uid], excluded[uid])
                check_result(result, request, Algorithm(algorithm), catalog - excluded[uid], models[algorithm].model_version)
                predictions[uid] = [item.movie_id for item in result.items]
                stream.write(json.dumps({'ml_user_id': uid, 'result': result.model_dump(mode='json')},
                                        ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
        metrics = compute_metrics(predictions, relevance, catalog)
        result = EvaluationResult(status='completed', runtime_mode='evaluation', algorithm=algorithm,
            model_version=models[algorithm].model_version, task_sha256=current_task['task_sha256'],
            model_manifest_sha256=sha256(paths[algorithm]), dataset_version=DATASET, split_version=SPLIT,
            schema_version=SCHEMA, partition=partition, user_count=metrics['user_count'],
            precision_at_10=metrics['precision_at_10'], recall_at_10=metrics['recall_at_10'])
        result_path = directory / (algorithm + '-result.json')
        result_path.write_text(result.model_dump_json(indent=2), encoding='utf-8')
        # Original teacher checkers validate exact fixed users, candidate exclusions, model bindings and DTO.
        checked_predictions = check_predictions(predictions_path, paths[algorithm], partition, data)
        checked_result = check_evaluation_result(result_path, paths[algorithm], data)
        checks = {'status': 'passed', 'model_binding': 'original_check_model_passed',
                  'predictions': checked_predictions, 'evaluation_result': 'original_check_evaluation_result_passed',
                  'declared_status': checked_result.status,
                  'note': '检查器通过仅证明协议合法；下列指标来自实际预测与留出正例交集，不代表达到成绩门槛。'}
        (directory / (algorithm + '-checks.json')).write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
        row = {'algorithm': algorithm, 'partition': partition, 'model_version': models[algorithm].model_version,
               **metrics, 'task_sha256': current_task['task_sha256'], 'model_manifest_sha256': sha256(paths[algorithm])}
        (directory / (algorithm + '-metrics.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        rows.append(row)
        # validation可在ALS尚未交付时明确选择非ALS算法并发布真实聚合，不伪造缺失模型。
        publish_aggregates(output_root, public_summary_path)
        print(json.dumps({'status': 'completed', **row, 'predictions': str(predictions_path.resolve())}, ensure_ascii=False), flush=True)
    return rows


def main(argv=None):
    import argparse
    from recommender.data import ContractError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partition', choices=['validation', 'test'], required=True)
    parser.add_argument('--algorithms', nargs='+', choices=ALGORITHMS, default=ALGORITHMS)
    parser.add_argument('--models-root', type=Path, default=ROOT / 'models')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'artifacts/evaluation')
    parser.add_argument('--public-summary-path', type=Path, default=ROOT / 'models/business/evaluation-summary.json')
    parser.add_argument('--freeze-only', action='store_true', help='明确冻结模型/参数/源码；本进程不读取test标签也不运行评价')
    args = parser.parse_args(argv)
    try:
        if args.freeze_only:
            path = freeze_models(args.models_root, args.output_root, args.algorithms)
            print(json.dumps({'status': 'frozen_before_test', 'path': str(path.resolve())}, ensure_ascii=False))
        else:
            evaluate_partition(args.partition, args.algorithms, models_root=args.models_root, output_root=args.output_root,
                               public_summary_path=args.public_summary_path)
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({'status': 'failed', 'code': getattr(exc, 'code', 'EVALUATION_FAILED'), 'message': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
