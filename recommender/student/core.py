"""可解释推荐核心：仅接受调用方明确传入的训练行，不读取任何数据文件/数据库。"""
import numpy as np


def fit_popular(movie_ids, ratings, prior=20.0):
    """用全局均分作先验，评分数量作支持度的 Bayesian 加权评分。"""
    index = {mid: i for i, mid in enumerate(movie_ids)}
    counts = np.zeros(len(movie_ids), dtype=np.int64)
    sums = np.zeros(len(movie_ids), dtype=np.float64)
    for _, mid, rating in ratings:
        if mid in index:
            counts[index[mid]] += 1
            sums[index[mid]] += float(rating)
    mean = float(sums.sum() / counts.sum()) if counts.sum() else 0.0
    return (sums + prior * mean) / (counts + prior), counts


def fit_genres(movie_ids, movie_genres):
    tokens = {mid: {g for g in movie_genres.get(mid, '').split('|')
                    if g and g not in {'NULL', '(no genres listed)'}} for mid in movie_ids}
    genres = sorted(set().union(*tokens.values()))
    columns = {g: i for i, g in enumerate(genres)}
    features = np.zeros((len(movie_ids), len(genres)), dtype=np.float64)
    for i, mid in enumerate(movie_ids):
        for genre in tokens[mid]:
            features[i, columns[genre]] = 1.0
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    return features / np.maximum(norms, 1.0), np.asarray(genres, dtype=str)


def content_scores(movie_ids, features, ratings):
    index = {mid: i for i, mid in enumerate(movie_ids)}
    positives = [(index[mid], float(rating) - 3.0) for mid, rating in ratings.items()
                 if mid in index and float(rating) >= 4.0]
    if not positives:
        return None
    profile = sum((features[i] * weight for i, weight in positives), np.zeros(features.shape[1]))
    norm = np.linalg.norm(profile)
    return features @ (profile / norm) if norm > 0 else None


def fit_itemcf(movie_ids, ratings, top_k=80, shrinkage=20.0):
    """正反馈二值共现；cosine×共现/(共现+shrinkage)，逐源电影保留Top邻居。"""
    from scipy import sparse
    index = {mid: i for i, mid in enumerate(movie_ids)}
    pairs = {(str(uid), index[mid]) for uid, mid, rating in ratings
             if mid in index and float(rating) >= 4.0}
    users = {uid: i for i, uid in enumerate(sorted({u for u, _ in pairs}))}
    rows = [users[u] for u, _ in pairs]
    cols = [i for _, i in pairs]
    binary = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(users), len(movie_ids)))
    support = np.asarray(binary.sum(axis=0)).ravel()
    numeric_ids = np.asarray(movie_ids, dtype=np.int64)
    out_rows, out_cols, out_values = [], [], []
    for start in range(0, len(movie_ids), 128):
        stop = min(start + 128, len(movie_ids))
        cooc = (binary[:, start:stop].T @ binary).toarray()
        denom = np.sqrt(support[start:stop, None] * support[None, :])
        similarities = np.divide(cooc, denom, out=np.zeros_like(cooc), where=denom > 0)
        similarities *= np.divide(cooc, cooc + shrinkage, out=np.zeros_like(cooc), where=cooc + shrinkage > 0)
        for local, source in enumerate(range(start, stop)):
            similarities[local, source] = 0.0
            candidates = np.flatnonzero(similarities[local] > 0)
            selected = candidates[np.lexsort((numeric_ids[candidates], -similarities[local, candidates]))[:top_k]]
            out_rows.extend([source] * len(selected))
            out_cols.extend(selected.tolist())
            out_values.extend(similarities[local, selected].tolist())
    return sparse.csr_matrix((out_values, (out_rows, out_cols)), shape=(len(movie_ids), len(movie_ids)))


def itemcf_scores(movie_ids, neighbors, ratings):
    index = {mid: i for i, mid in enumerate(movie_ids)}
    positives = [(index[mid], float(rating) - 3.0) for mid, rating in ratings.items()
                 if mid in index and float(rating) >= 4.0]
    if not positives:
        return None
    scores = np.asarray(neighbors[[i for i, _ in positives]].T @ np.asarray([w for _, w in positives])).ravel()
    return scores if np.any(scores > 0) else None


def rank_scores(movie_ids, scores, excluded, limit):
    """有限分数降序；任何并列严格按数值电影ID升序。"""
    values = np.asarray(scores, dtype=np.float64)
    ids = np.asarray(movie_ids, dtype=str)
    allowed = np.fromiter((mid not in excluded for mid in movie_ids), dtype=bool, count=len(ids))
    selected = np.flatnonzero(np.isfinite(values) & allowed)
    numeric = ids[selected].astype(np.int64)
    ranked = selected[np.lexsort((numeric, -values[selected]))[:limit]]
    return [(str(ids[i]), float(values[i])) for i in ranked]
