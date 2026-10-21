"""独立 Spark MLlib ALS 训练与便携因子导出；不会在 API 中启动 Spark。"""
from pathlib import Path
import json
import csv
import math

from recommender.data import DATASET, SPLIT, SCHEMA, MANIFEST_SHA, FrozenData, require, sha256
from recommender.validation import ModelManifest, check_model

import numpy as np


def export_factors(path: Path, catalog, item_rows, user_rows, *, rank: int):
    """保持 Spark 原始因子数值，仅按数值 ID 重排。"""
    if type(rank) is not int or rank < 1:
        raise ValueError("因子 rank 必须是正整数")
    catalog = list(catalog)
    if not catalog or any(not isinstance(i, str) or not i.isascii() or
                          not i.isdecimal() or str(int(i)) != i or int(i) <= 0
                          for i in catalog) or len(set(catalog)) != len(catalog):
        raise ValueError("目录 ID 必须唯一且为规范正整数字符串")
    matrices = []
    for rows in (item_rows, user_rows):
        factors = {}
        for identity, features in rows:
            key = str(identity)
            if (not key.isascii() or not key.isdecimal() or str(int(key)) != key or
                    not 0 < int(key) <= 2**31 - 1 or key in factors):
                raise ValueError("Spark 因子 ID 非法或重复")
            vector = np.asarray(features, dtype=np.float32)
            if vector.shape != (rank,) or not np.isfinite(vector).all():
                raise ValueError("Spark 因子维数不符或含非有限值")
            factors[key] = vector
        if not factors:
            raise ValueError("Spark 因子不能为空")
        ids = sorted(factors, key=int)
        matrices.append((ids, np.stack([factors[i] for i in ids])))
    (movie_ids, item_factors), (user_ids, user_factors) = matrices
    if set(movie_ids) != set(catalog):
        raise ValueError("Spark 电影因子必须与批准目录完全一致，拒绝缺失、补零或目录外电影")
    np.savez_compressed(
        path,
        movie_ids=np.asarray(movie_ids, dtype=str),
        item_factors=item_factors,
        user_ids=np.asarray(user_ids, dtype=str),
        user_factors=user_factors,
    )


def write_manifest(output_dir: Path, *, data, mode: str, parameters: dict,
                   tool_versions: dict[str, str]) -> Path:
    """使用原始 ModelManifest，递归绑定 Spark 保存目录内的每个实际文件。"""
    if mode not in {"evaluation", "business"}:
        raise ValueError("训练模式非法")
    base = Path(output_dir).resolve()
    npz = base / "model.npz"
    spark_dir = base / "spark-model"
    if not npz.is_file() or not spark_dir.is_dir():
        raise ValueError("缺少真实 model.npz 或 spark-model")
    files = [npz] + sorted(spark_dir.rglob("*"))
    artifacts = {}
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise ValueError("模型工件不得为链接或越界路径")
        if path.is_file():
            artifacts[path.relative_to(base).as_posix()] = sha256(path)
    if len(artifacts) < 2:
        raise ValueError("spark-model 目录为空")
    training = "evaluation/train.csv" if mode == "evaluation" else "business/history_ratings.csv"
    manifest = ModelManifest(
        algorithm="als", model_version="als-v1", runtime_mode=mode,
        dataset_version=DATASET, split_version=SPLIT, schema_version=SCHEMA,
        manifest_sha256=MANIFEST_SHA,
        training_files={training: data.manifest["files"][training]["sha256"]},
        feature_files={"training-catalog.csv": data.manifest["files"]["training-catalog.csv"]["sha256"]},
        artifacts=artifacts, parameters=parameters, tool_versions=tool_versions,
    )
    path = base / "manifest.json"
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    check_model(path, mode, "als", data)
    return path


def load_training(mode: str, *, data=None):
    """只读本模式批准的评分文件；evaluation 必须调用 FrozenData.training()。"""
    if mode not in {"evaluation", "business"}:
        raise ValueError("训练模式非法")
    data = data if data is not None else FrozenData()
    catalog = data.catalog()
    if mode == "evaluation":
        source = data.training()
    else:
        # FrozenData 已先验证正式 manifest。业务读取不能扩展 evaluation 白名单。
        name = "business/history_ratings.csv"
        expected = data.manifest["files"][name]
        path = (data.package / name).resolve()
        require(path.is_relative_to(data.package) and path.is_file(),
                "业务评分路径不存在或越界", "DATA_IDENTITY_INVALID")
        require(path.stat().st_size == expected["bytes"] and sha256(path) == expected["sha256"],
                "业务评分摘要不符", "DATA_IDENTITY_INVALID")

        def business_rows():
            count = 0
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                require(reader.fieldnames == expected["columns"], "业务评分字段不符", "DATA_IDENTITY_INVALID")
                for row in reader:
                    require(None not in row and all(v is not None for v in row.values()),
                            "业务评分字段不完整", "DATA_IDENTITY_INVALID")
                    count += 1
                    yield row
            require(count == expected["records"], "业务评分行数不符", "DATA_IDENTITY_INVALID")

        source = business_rows()
    rows = []
    for row in source:
        user, movie, rating = int(row["userId"]), int(row["movieId"]), float(row["rating"])
        require(0 < user <= 2**31 - 1 and 0 < movie <= 2**31 - 1 and
                str(movie) in catalog and math.isfinite(rating) and 0.5 <= rating <= 5.0,
                "训练评分值或 ID 非法", "DATA_IDENTITY_INVALID")
        rows.append((user, movie, rating))
    expected_count = 583502 if mode == "evaluation" else 798775
    require(len(rows) == expected_count, "训练评分数量不符", "DATA_IDENTITY_INVALID")
    require({str(movie) for _, movie, _ in rows} == catalog,
            "训练评分未覆盖全部批准目录", "DATA_IDENTITY_INVALID")
    return catalog, rows


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="隔离运行 Spark MLlib 显式评分 ALS；不读取验证/测试标签")
    parser.add_argument("--mode", required=True, choices=("business", "evaluation"))
    parser.add_argument("--output-root", type=Path, default=Path("models"), help="输出 <root>/<mode>/als-v1")
    parser.add_argument("--rank", type=int, choices=(24, 32), default=24)
    parser.add_argument("--master", choices=("local[1]", "local[2]", "local[4]"), default="local[2]")
    parser.add_argument("--driver-memory", default="2g")
    return parser


def fit_als(frame, *, rank=24):
    """实际调用 Spark MLlib；返回真实模型和 estimator 的完整参数快照。"""
    from pyspark.ml.recommendation import ALS
    if rank not in {24, 32}:
        raise ValueError("ALS rank 只允许预先固定的 24 或 32")
    estimator = ALS(
        rank=rank, maxIter=8, regParam=0.1, seed=42,
        implicitPrefs=False, nonnegative=False, alpha=1.0,
        userCol="userId", itemCol="movieId", ratingCol="rating",
        coldStartStrategy="nan",
        numUserBlocks=4, numItemBlocks=4, blockSize=4096,
        checkpointInterval=-1, intermediateStorageLevel="MEMORY_AND_DISK",
        finalStorageLevel="MEMORY_AND_DISK",
    )
    parameters = {param.name: value for param, value in estimator.extractParamMap().items()}
    return estimator.fit(frame), parameters


def train(mode: str, *, output_root=Path("models"), rank=24,
          master="local[2]", driver_memory="2g") -> Path:
    """每个进程只训练一个模式，保留 Spark 原模型并导出对齐 NPZ。"""
    import importlib.metadata
    import platform
    import tempfile
    import time
    from datetime import datetime, timezone

    if mode not in {"business", "evaluation"} or rank not in {24, 32}:
        raise ValueError("训练模式或 rank 非法")
    if master not in {"local[1]", "local[2]", "local[4]"}:
        raise ValueError("仅允许独立本地训练，不向外部 Spark 集群发送课程评分")
    target = Path(output_root).resolve() / mode / "als-v1"
    if target.exists():
        raise FileExistsError(f"拒绝覆盖已有模型：{target}；如需重跑请指定新的 --output-root")
    # 依赖延迟导入，API/普通导出测试不需要 Java 或 pyspark。
    import pyspark
    from pyspark.sql import SparkSession
    from pyspark.ml.recommendation import ALSModel

    start = time.monotonic()
    data = FrozenData()
    catalog, rows = load_training(mode, data=data)
    user_ids = {str(user) for user, _, _ in rows}
    events = [{"event": "input_verified", "mode": mode, "ratings": len(rows),
               "movies": len(catalog), "users": len(user_ids),
               "utc": datetime.now(timezone.utc).isoformat()}]
    print(json.dumps(events[-1], ensure_ascii=False), flush=True)
    spark = (SparkSession.builder.master(master).appName(f"cinelab-{mode}-als-v1")
             .config("spark.driver.memory", driver_memory)
             .config("spark.ui.enabled", "false")
             .config("spark.sql.shuffle.partitions", "4")
             .config("spark.default.parallelism", "4")
             .config("spark.driver.bindAddress", "127.0.0.1")
             .config("spark.driver.host", "127.0.0.1")
             .getOrCreate())
    try:
        spark.sparkContext.setLogLevel("WARN")
        java_version = spark.sparkContext._jvm.java.lang.System.getProperty("java.version")
        if not java_version.startswith("17."):
            raise RuntimeError(f"本训练器固定 Java17；实际为 {java_version}，不要使用宿主 Java25")
        versions = {
            "python": platform.python_version(), "pyspark": pyspark.__version__,
            "spark": spark.version, "java": java_version,
            "scala": spark.sparkContext._jvm.scala.util.Properties.versionNumberString(),
            "hadoop": spark.sparkContext._jvm.org.apache.hadoop.util.VersionInfo.getVersion(),
            "numpy": np.__version__, "pydantic": importlib.metadata.version("pydantic"),
        }
        frame = spark.createDataFrame(rows, "userId int, movieId int, rating float")
        # 固定分区与分区内顺序，避免无序输入导致的随机初始状态变化。
        frame = frame.repartition(4, "userId").sortWithinPartitions("userId", "movieId").cache()
        require(frame.count() == len(rows), "Spark 评分行数与批准输入不符", "DATA_IDENTITY_INVALID")
        rating_count = len(rows)
        del rows
        print(json.dumps({"event": "spark_fit_start", "mode": mode, "tool_versions": versions,
                          "rank": rank, "maxIter": 8, "regParam": 0.1, "seed": 42,
                          "implicitPrefs": False, "nonnegative": False}, ensure_ascii=False), flush=True)
        model, parameters = fit_als(frame, rank=rank)
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".als-v1-", dir=target.parent))
        spark_path = stage / "spark-model"
        model.write().save(str(spark_path))
        # 回读真实 Spark 工件，再从回读的因子生成便携 NPZ。
        restored = ALSModel.load(str(spark_path))
        item_rows = [(row.id, row.features) for row in restored.itemFactors.collect()]
        user_rows = [(row.id, row.features) for row in restored.userFactors.collect()]
        require({str(identity) for identity, _ in user_rows} == user_ids,
                "Spark 用户因子与真实训练用户不一致", "MODEL_BINDING_INVALID")
        export_factors(stage / "model.npz", catalog, item_rows, user_rows, rank=rank)
        parameters.update({"training_ratings": rating_count, "training_movies": len(catalog),
                           "training_users": len(user_ids), "master": master,
                           "driver_memory": driver_memory, "input_partitions": 4})
        events.append({"event": "spark_model_saved_and_reloaded", "mode": mode,
                       "ratings": rating_count, "movies": len(item_rows), "users": len(user_rows),
                       "elapsed_seconds": round(time.monotonic() - start, 3),
                       "tool_versions": versions, "parameters": parameters,
                       "utc": datetime.now(timezone.utc).isoformat()})
        (stage / "training.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_path = write_manifest(stage, data=data, mode=mode, parameters=parameters, tool_versions=versions)
        # 训练日志也是模型工件；用同一原始 schema 追加绑定。
        manifest = ModelManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        manifest.artifacts["training.json"] = sha256(stage / "training.json")
        manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        check_model(manifest_path, mode, "als", data)
        if target.exists():
            raise FileExistsError(f"模型目录被另一进程创建，拒绝覆盖：{target}")
        stage.rename(target)
        check_model(target / "manifest.json", mode, "als", data)
        print(json.dumps({**events[-1], "manifest": str(target / "manifest.json"),
                          "model_npz": str(target / "model.npz")}, ensure_ascii=False), flush=True)
        return target / "manifest.json"
    finally:
        spark.stop()


if __name__ == "__main__":
    args = build_parser().parse_args()
    train(args.mode, output_root=args.output_root, rank=args.rank,
          master=args.master, driver_memory=args.driver_memory)
