"""五种算法的同步Provider。构造/导入不训练、不读取模型、不连接数据库。"""
from pathlib import Path

from recommender.data import ContractError, FrozenData, require
from recommender.validation import check_model, check_request


class StudentProvider:
    def __init__(self, algorithm: str, model_manifest_path: Path):
        if algorithm not in {'popular', 'content', 'itemcf', 'cold_start', 'als'}:
            raise ValueError('未知推荐算法')
        self.algorithm = algorithm
        self.model_manifest_path = Path(model_manifest_path)
        self._model = None
        self._arrays = None

    def _load(self, mode):
        if self._model is not None:
            require(self._model.runtime_mode == mode, '不能跨business/evaluation使用模型', 'MODEL_BINDING_INVALID')
            return
        import numpy as np
        model = check_model(self.model_manifest_path, mode, self.algorithm)
        require('model.npz' in model.artifacts, '缺少真实model.npz工件', 'MODEL_BINDING_INVALID')
        try:
            with np.load(self.model_manifest_path.parent / 'model.npz', allow_pickle=False) as archive:
                arrays = {key: archive[key] for key in archive.files}
            ids = arrays['movie_ids']
            require(ids.ndim == 1 and ids.dtype.kind == 'U' and len(ids) > 0 and
                    list(ids) == sorted(set(ids.tolist()), key=int) and set(ids) <= FrozenData().catalog(),
                    '模型电影ID须为批准目录中的数值升序字符串', 'MODEL_BINDING_INVALID')
            if self.algorithm in {'content', 'cold_start'}:
                require(arrays['genre_features'].ndim == 2 and len(arrays['genre_features']) == len(ids),
                        '内容特征维度不符', 'MODEL_BINDING_INVALID')
            if self.algorithm in {'itemcf', 'cold_start'} and 'neighbor_data' in arrays:
                from scipy import sparse
                self._neighbors = sparse.csr_matrix((arrays['neighbor_data'], arrays['neighbor_indices'], arrays['neighbor_indptr']),
                                                    shape=(len(ids), len(ids)))
            for key, value in arrays.items():
                if value.dtype.kind in 'fci':
                    require(np.isfinite(value).all(), '模型含非有限数值：' + key, 'MODEL_BINDING_INVALID')
        except (OSError, KeyError, ValueError, TypeError, IndexError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise ContractError('MODEL_BINDING_INVALID', '模型工件缺失或结构非法') from exc
        self._arrays = arrays
        self._model = model

    def recommend(self, request, context=None):
        check_request(request)
        require(request.runtime_mode == 'business', '评价必须显式注入允许的历史', 'DATA_MODE_INVALID')
        from recommender.business import read_history
        history = read_history(request)
        interests = [g for g in ((context or {}).get('interest_genres') or []) if isinstance(g, str)]
        return self.recommend_history(request, {row.movie_id: float(row.rating) for row in history.ratings},
                                      interest_genres=interests)

    def recommend_history(self, request, ratings: dict[str, float], excluded: set[str] | None = None,
                          interest_genres: list[str] | None = None):
        """无DB离线入口：画像只用ratings，excluded仅用于过滤，不能进入拟合或画像。"""
        from datetime import datetime, timezone
        import numpy as np
        from recommender.contracts import Algorithm, RecommendationItem, RecommendationResult
        from .core import content_scores, interest_scores, itemcf_scores, rank_scores
        check_request(request)
        self._load(request.runtime_mode)
        arrays = self._arrays
        ids = arrays['movie_ids'].tolist()
        id_set = set(ids)
        excluded = set(excluded or ()) | set(ratings)
        require(all(isinstance(mid, str) and np.isfinite(value) and 0.5 <= float(value) <= 5.0
                    for mid, value in ratings.items()), '评分字典非法', 'REQUEST_CONTEXT_INVALID')
        source = 'popular:bayesian_rating_support'
        scores = None
        if self.algorithm == 'content':
            scores = content_scores(ids, arrays['genre_features'], ratings)
            source = 'content:positive_genre_cosine'
        elif self.algorithm == 'itemcf':
            scores = itemcf_scores(ids, self._neighbors, ratings)
            source = 'itemcf:positive_binary_cosine_shrinkage'
        elif self.algorithm == 'cold_start':
            positives = sum(float(v) >= 4.0 for mid, v in ratings.items() if mid in id_set)
            if positives >= 5 and hasattr(self, '_neighbors'):
                scores = itemcf_scores(ids, self._neighbors, ratings)
                source = 'cold_start:itemcf_five_or_more_positive_ratings'
            if scores is None:
                scores = content_scores(ids, arrays['genre_features'], ratings)
                source = 'cold_start:content_few_positive_ratings'
            if scores is None and interest_genres:
                scores = interest_scores(ids, arrays['genre_features'], arrays['genres'], interest_genres)
                source = 'cold_start:interest_genre_profile'
        elif self.algorithm == 'als':
            scores, source = self._als_scores(request, ratings)
        if self.algorithm != 'popular' and scores is not None and self.algorithm != 'als':
            scores = np.where(scores > 0, scores, np.nan)
            if not any(mid not in excluded and np.isfinite(value) for mid, value in zip(ids, scores)):
                scores = None
        if scores is None:
            scores = self._popular_scores(request.runtime_mode)
            if self.algorithm != 'popular':
                has_positive = any(mid in id_set and float(v) >= 4.0 for mid, v in ratings.items())
                reason = 'no_personalized_candidates' if has_positive else 'no_positive_profile'
                source = 'popular_fallback:' + reason
        ranked = rank_scores(ids, scores, excluded, request.limit)
        algorithm, version = Algorithm(self.algorithm), self._model.model_version
        items = []
        for rank, (mid, score) in enumerate(ranked, 1):
            sources = [source]
            if source == 'cold_start:interest_genre_profile':
                sources.append('profile:interest_genres')
            elif source.startswith(('content:', 'itemcf:', 'cold_start:')):
                sources.append('profile:explicit_ratings_ge_4')
            items.append(RecommendationItem(movie_id=mid, rank=rank, algorithm=algorithm,
                         model_version=version, score=score, recall_sources=sources, recall_ranks={source: rank}))
        return RecommendationResult(algorithm=algorithm, model_version=version, dataset_version=request.dataset_version,
            split_version=request.split_version, runtime_mode=request.runtime_mode, generated_at=datetime.now(timezone.utc), items=items)

    def _als_scores(self, request, ratings):
        import numpy as np
        arrays = self._arrays
        ids = arrays['movie_ids'].tolist()
        index = {mid: i for i, mid in enumerate(ids)}
        require({'item_factors', 'user_ids', 'user_factors'} <= arrays.keys(),
                'ALS必须提供真实用户/电影因子', 'MODEL_BINDING_INVALID')
        items, users = arrays['item_factors'], arrays['user_factors']
        user_ids = arrays['user_ids'].tolist()
        require(items.ndim == users.ndim == 2 and items.shape[0] == len(ids) and
                users.shape == (len(user_ids), items.shape[1]) and items.shape[1] > 0 and
                user_ids == sorted(set(user_ids), key=int), 'ALS因子维度/用户ID非法', 'MODEL_BINDING_INVALID')
        if not any(mid in index and float(value) >= 4.0 for mid, value in ratings.items()):
            return None, 'popular_fallback:no_positive_profile'
        if request.subject_kind == 'ml' and str(request.subject_id) in user_ids:
            vector = users[user_ids.index(str(request.subject_id))]
            return items @ vector, 'als:trained_ml_user_factors'
        observed = [(index[mid], float(value)) for mid, value in ratings.items() if mid in index]
        if not observed:
            return None, 'popular_fallback:no_positive_profile'
        factors = items[[i for i, _ in observed]].astype(np.float64)
        values = np.asarray([value for _, value in observed])
        regularization = float(self._model.parameters.get('fold_in_regularization', 0.1))
        require(np.isfinite(regularization) and regularization > 0, 'ALS fold-in正则参数非法', 'MODEL_BINDING_INVALID')
        vector = np.linalg.solve(factors.T @ factors + regularization * len(observed) * np.eye(items.shape[1]), factors.T @ values)
        return items @ vector, 'als:ridge_fold_in'

    def _popular_scores(self, mode):
        if 'popular_scores' in self._arrays:
            return self._arrays['popular_scores']
        # ALS训练器可不重复热门统计；仅从同模式、独立校验的真实popular工件读取。
        other = StudentProvider('popular', self.model_manifest_path.parent.parent / 'popular-v1/manifest.json')
        other._load(mode)
        require(other._arrays['movie_ids'].tolist() == self._arrays['movie_ids'].tolist(),
                'ALS与热门目录不一致', 'MODEL_BINDING_INVALID')
        self._arrays['popular_scores'] = other._arrays['popular_scores']
        return self._arrays['popular_scores']
