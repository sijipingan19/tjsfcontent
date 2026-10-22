"""独立业务/评价训练进程；评价拟合唯一入口是FrozenData.training()。不训练ALS。"""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
import platform

import numpy as np
import scipy

from recommender.data import DATASET, SPLIT, SCHEMA, MANIFEST_SHA, FrozenData, require, sha256
from recommender.validation import ModelManifest, check_model
from .core import fit_popular, fit_genres, fit_itemcf

ROOT = Path(__file__).resolve().parents[2]
PARAMETERS = {'popular_positive_threshold': 4.0, 'itemcf_neighbors': 80, 'itemcf_shrinkage': 20.0,
              'positive_threshold': 4.0, 'profile_weight': 'rating-3', 'cold_cf_min_positives': 5,
              'genre_encoding': 'row_l2_normalized_multihot', 'tie_break': 'numeric_movie_id_ascending'}


def business_training(data):
    """只在明确business独立训练进程中读取封存业务快照；不扩展FrozenData白名单。"""
    name = 'business/history_ratings.csv'
    expected = data.manifest['files'][name]
    path = (data.package / name).resolve()
    require(path.is_relative_to(data.package.resolve()) and path.is_file() and
            path.stat().st_size == expected['bytes'] and sha256(path) == expected['sha256'],
            '业务快照身份或摘要不符', 'DATA_IDENTITY_INVALID')
    count = 0
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == expected['columns'], '业务快照CSV字段不符', 'DATA_IDENTITY_INVALID')
        for row in reader:
            require(None not in row and all(v is not None for v in row.values()), '业务快照CSV行不完整', 'DATA_IDENTITY_INVALID')
            count += 1
            yield row
    require(count == expected['records'], '业务快照CSV行数不符', 'DATA_IDENTITY_INVALID')


def train_mode(mode, output_root=ROOT / 'models', *, data=None):
    require(mode in {'business', 'evaluation'}, '必须明确训练模式', 'DATA_MODE_INVALID')
    data = data or FrozenData()
    movie_ids = sorted(data.catalog(), key=int)
    # 不读取validation/test/cold/support/query；业务和评价从不合并训练。
    source = data.training() if mode == 'evaluation' else business_training(data)
    ratings = [(row['userId'], row['movieId'], float(row['rating'])) for row in source]
    catalog = set(movie_ids)
    require(all(mid in catalog and np.isfinite(value) and 0.5 <= value <= 5.0 for _, mid, value in ratings),
            '训练评分含目录外ID或非法数值', 'DATA_IDENTITY_INVALID')
    movies = {row['movie_id']: row['genres'] for row in data.rows('movies.csv')}
    scores, counts = fit_popular(movie_ids, ratings, positive_threshold=PARAMETERS['popular_positive_threshold'])
    features, genres = fit_genres(movie_ids, movies)
    neighbors = fit_itemcf(movie_ids, ratings, top_k=PARAMETERS['itemcf_neighbors'], shrinkage=PARAMETERS['itemcf_shrinkage'])
    common = {'movie_ids': np.asarray(movie_ids, dtype=str), 'popular_scores': scores, 'rating_counts': counts}
    content = {'genre_features': features, 'genres': genres}
    cf = {'neighbor_data': neighbors.data, 'neighbor_indices': neighbors.indices, 'neighbor_indptr': neighbors.indptr}
    arrays_by_algorithm = {'popular': common, 'content': {**common, **content},
                          'itemcf': {**common, **cf}, 'cold_start': {**common, **content, **cf}}
    training_name = 'evaluation/train.csv' if mode == 'evaluation' else 'business/history_ratings.csv'
    parameters_by_algorithm = {
        'popular': {'ranking': 'positive_support_desc',
                    'positive_threshold': PARAMETERS['popular_positive_threshold']},
        'content': {'encoding': PARAMETERS['genre_encoding'], 'profile_weight': PARAMETERS['profile_weight']},
        'itemcf': {'neighbors': PARAMETERS['itemcf_neighbors'], 'shrinkage': PARAMETERS['itemcf_shrinkage'],
                   'interaction': 'binary_rating_ge_4', 'similarity': 'cosine_times_cooccurrence_over_cooccurrence_plus_shrinkage',
                   'profile_weight': PARAMETERS['profile_weight']},
        'cold_start': dict(PARAMETERS)}
    paths = {}
    for algorithm, arrays in arrays_by_algorithm.items():
        directory = Path(output_root) / mode / (algorithm + '-v1')
        directory.mkdir(parents=True, exist_ok=True)
        artifact = directory / 'model.npz'
        np.savez_compressed(artifact, **arrays)
        feature_names = ['training-catalog.csv'] + (['movies.csv'] if algorithm in {'content', 'cold_start'} else [])
        manifest = ModelManifest(algorithm=algorithm, model_version=algorithm + '-v1', runtime_mode=mode,
            dataset_version=DATASET, split_version=SPLIT, schema_version=SCHEMA, manifest_sha256=MANIFEST_SHA,
            training_files={training_name: data.manifest['files'][training_name]['sha256']},
            feature_files={name: data.manifest['files'][name]['sha256'] for name in feature_names},
            artifacts={'model.npz': sha256(artifact)},
            parameters={**parameters_by_algorithm[algorithm], 'positive_threshold': 4.0,
                        'training_rows': len(ratings), 'training_users': len({uid for uid, _, _ in ratings}),
                        'catalog_count': len(movie_ids), 'tie_break': PARAMETERS['tie_break']},
            tool_versions={'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__})
        path = directory / 'manifest.json'
        path.write_text(manifest.model_dump_json(indent=2), encoding='utf-8')
        check_model(path, mode, algorithm)
        paths[algorithm] = path
    record = {'status': 'trained_four_non_als_models', 'runtime_mode': mode,
              'generated_at': datetime.now(timezone.utc).isoformat(), 'training_rows': len(ratings),
              'training_users': len({uid for uid, _, _ in ratings}), 'catalog_count': len(movie_ids),
              'neighbor_nonzeros': neighbors.nnz, 'genre_count': len(genres),
              'models': {algorithm: str(path.resolve()) for algorithm, path in paths.items()},
              'validation': 'original_check_model_passed; this_is_not_an_effectiveness_claim'}
    (Path(output_root) / mode / 'training-record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['business', 'evaluation'], required=True)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'models')
    args = parser.parse_args(argv)
    paths = train_mode(args.mode, args.output_root)
    print(json.dumps({'status': 'completed', 'runtime_mode': args.mode,
                      'models': {algorithm: str(path.resolve()) for algorithm, path in paths.items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
