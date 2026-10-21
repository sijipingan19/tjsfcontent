"""Independent weighted Reciprocal Rank Fusion; source scores are never mixed."""
import math


def fuse(sources, *, weights, excluded, catalog, limit):
    """Preserve source ranks before filtering; deterministic numeric-ID tie break."""
    if len(sources) < 2 or set(sources) != set(weights):
        raise ValueError('RRF requires at least two explicitly weighted sources')
    if not 1 <= limit <= 50 or any(not math.isfinite(w) or w <= 0 for w in weights.values()):
        raise ValueError('Invalid RRF weights or limit')
    candidates = {}
    for source, movie_ids in sources.items():
        seen = set()
        for rank, movie_id in enumerate(movie_ids, 1):
            if movie_id in seen:
                continue
            seen.add(movie_id)
            if movie_id not in catalog or movie_id in excluded:
                continue
            item = candidates.setdefault(movie_id, {'movie_id': movie_id, 'score': 0.0,
                                                     'recall_sources': [], 'recall_ranks': {}})
            item['score'] += weights[source] / (60 + rank)
            item['recall_sources'].append(source)
            item['recall_ranks'][source] = rank
    ordered = sorted(candidates.values(), key=lambda x: (-x['score'], int(x['movie_id'])))[:limit]
    return [dict(item, rank=rank) for rank, item in enumerate(ordered, 1)]


def rerank(base, profile, *, catalog, excluded, limit):
    """Explainable short-term genre adjustment of genuine base candidates only."""
    adjusted = []
    for item in base:
        movie_id = item['movie_id']
        if movie_id in excluded or movie_id not in catalog:
            continue
        genres = [g for g in catalog[movie_id] if g and g != '(no genres listed)']
        boost = 0.05 * sum(profile.get(g, 0.0) for g in genres) / len(genres) if genres else 0.0
        sources = list(item['recall_sources'])
        if boost:
            sources.append('short_term_genres')
        adjusted.append(dict(item, base_rank=item['rank'], base_score=item['score'],
                             short_term_boost=boost, score=item['score']+boost,
                             recall_sources=sources))
    adjusted.sort(key=lambda x: (-x['score'], int(x['movie_id'])))
    return [dict(item, rank=rank) for rank, item in enumerate(adjusted[:limit], 1)]
