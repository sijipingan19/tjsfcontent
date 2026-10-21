"""只读取离线程序已经产出的聚合评价，不访问评分文件、不执行训练。"""
import json
import math
from pathlib import Path

from recommender.data import DATASET, SPLIT


class SummaryNotReady(ValueError):
    """尚未计算实际评价结果。"""


class SummaryInvalid(ValueError):
    """摘要不是本课程完整、有效的四算法评价。"""


def load_summary(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise SummaryNotReady('尚未生成真实离线评价摘要')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        raise SummaryInvalid('评价摘要无法读取或不是有效 JSON') from None
    if (not isinstance(value, dict) or value.get('status') != 'completed'
            or value.get('dataset_version') != DATASET or value.get('split_version') != SPLIT
            or not isinstance(value.get('items'), list) or not value['items']):
        raise SummaryInvalid('评价摘要尚未完成或数据身份不符')
    groups = {}
    for row in value['items']:
        if not isinstance(row, dict):
            raise SummaryInvalid('评价行格式不符')
        partition = row.get('partition')
        algorithm = row.get('algorithm')
        count = {'validation': 4209, 'test': 4198}.get(partition)
        if (count is None or type(row.get('user_count')) is not int or row['user_count'] != count
                or algorithm not in {'popular', 'content', 'itemcf', 'als'}
                or not isinstance(row.get('model_version'), str) or not row['model_version']):
            raise SummaryInvalid('算法、固定用户数、分区或模型版本不符')
        algorithms = groups.setdefault(partition, set())
        if algorithm in algorithms:
            raise SummaryInvalid('评价算法与分区不能重复')
        algorithms.add(algorithm)
        for metric in ['precision_at_10', 'recall_at_10'] + (['coverage_at_10'] if 'coverage_at_10' in row else []):
            score = row.get(metric)
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise SummaryInvalid('评价指标必须是实际计算的有限 [0,1] 数值')
    if any(algorithms != {'popular', 'content', 'itemcf', 'als'} for algorithms in groups.values()):
        raise SummaryInvalid('每个分区必须包含四种算法，不能把未完成算法隐藏')
    return value
