"""Spark 因子导出契约；微型矩阵只是测试样例，不是课程训练模型。"""
import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


class SparkExportTests(unittest.TestCase):
    def trainer(self):
        self.assertIsNotNone(importlib.util.find_spec("recommender.student.train_als"),
                             "缺少独立的真实 Spark ALS 训练器")
        return importlib.import_module("recommender.student.train_als")

    def test_export_aligns_real_factor_rows_by_numeric_ids(self):
        trainer = self.trainer()
        import numpy as np
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.npz"
            trainer.export_factors(
                path, {"10", "2", "1"},
                [(10, [10.0, 0.1]), (1, [1.0, 0.3]), (2, [2.0, 0.2])],
                [(20, [20.0, 0.5]), (3, [3.0, 0.4])], rank=2,
            )
            with np.load(path, allow_pickle=False) as model:
                self.assertEqual(model["movie_ids"].tolist(), ["1", "2", "10"])
                self.assertEqual(model["user_ids"].tolist(), ["3", "20"])
                np.testing.assert_allclose(model["item_factors"], [[1, .3], [2, .2], [10, .1]])
                np.testing.assert_allclose(model["user_factors"], [[3, .4], [20, .5]])
                self.assertEqual(model["movie_ids"].dtype.kind, "U")
                self.assertEqual(model["item_factors"].dtype, np.float32)

    def test_export_rejects_incomplete_or_corrupt_spark_factors(self):
        trainer = self.trainer()
        cases = [
            ([(1, [1., 2.])], [(3, [1., 2.])]),  # 缺失目录电影，不能补零冒充因子
            ([(1, [1., 2.]), (2, [2., 3.]), (4, [4., 5.])], [(3, [1., 2.])]),
            ([(1, [1., 2.]), (2, [2., 3.]), (2, [3., 4.])], [(3, [1., 2.])]),
            ([(1, [float("nan"), 2.]), (2, [2., 3.])], [(3, [1., 2.])]),
            ([(1, [1.]), (2, [2.])], [(3, [1., 2.])]),
            ([(1, [1., 2.]), (2, [2., 3.])], []),
            ([(1, [1., 2.]), (2, [2., 3.])], [(3, [1., 2.]), (3, [3., 4.])]),
        ]
        for items, users in cases:
            with self.subTest(items=items, users=users), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "model.npz"
                with self.assertRaisesRegex(ValueError, "因子|目录|ID"):
                    trainer.export_factors(path, {"1", "2"}, items, users, rank=2)
                self.assertFalse(path.exists(), "非法矩阵不得落盘")

    def test_manifest_binds_mode_and_hashes_every_spark_file(self):
        trainer = self.trainer()
        from recommender.data import FrozenData, sha256, ContractError
        from recommender.validation import check_model
        self.assertTrue(hasattr(trainer, "write_manifest"), "尚未实现原 ModelManifest 侧车")
        data = FrozenData()
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "model.npz").write_bytes(b"PROTOCOL TEST ONLY; NOT A MODEL")
            nested = base / "spark-model" / "itemFactors" / "part-00000.parquet"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"PROTOCOL TEST ONLY; NOT SPARK OUTPUT")
            for mode in ("evaluation", "business"):
                path = trainer.write_manifest(
                    base, data=data, mode=mode, parameters={"rank": 24},
                    tool_versions={"test": "PROTOCOL-ONLY"},
                )
                manifest = check_model(path, mode, "als", data)
                training = "evaluation/train.csv" if mode == "evaluation" else "business/history_ratings.csv"
                self.assertEqual(manifest.training_files, {training: data.manifest["files"][training]["sha256"]})
                self.assertEqual(manifest.feature_files, {"training-catalog.csv": data.manifest["files"]["training-catalog.csv"]["sha256"]})
                self.assertEqual(manifest.artifacts, {"model.npz": sha256(base / "model.npz"),
                                                     "spark-model/itemFactors/part-00000.parquet": sha256(nested)})
                with self.assertRaises(ContractError):
                    check_model(path, "business" if mode == "evaluation" else "evaluation", "als", data)
            nested.write_bytes(b"tampered")
            with self.assertRaises(ContractError):
                check_model(path, "business", "als", data)

    def test_manifest_refuses_missing_npz_or_empty_spark_model(self):
        trainer = self.trainer()
        from recommender.data import FrozenData
        self.assertTrue(hasattr(trainer, "write_manifest"), "尚未实现侧车约束")
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for create_npz in (False, True):
                if create_npz:
                    (base / "model.npz").write_bytes(b"PROTOCOL TEST ONLY")
                with self.assertRaises(ValueError):
                    trainer.write_manifest(base, data=FrozenData(), mode="evaluation",
                                           parameters={}, tool_versions={"test": "PROTOCOL-ONLY"})
                self.assertFalse((base / "manifest.json").exists())

    def test_training_reader_uses_only_approved_mode_file(self):
        trainer = self.trainer()
        from recommender.data import FrozenData
        self.assertTrue(hasattr(trainer, "load_training"), "尚未实现隔离训练数据入口")

        class AuditedData(FrozenData):
            def __init__(self):
                super().__init__()
                self.opened = []
                self.training_called = False

            def rows(self, name, *, evaluation=False):
                self.opened.append(name)
                return super().rows(name, evaluation=evaluation)

            def training(self):
                self.training_called = True
                return super().training()

        for mode, expected in (("evaluation", 583502), ("business", 798775)):
            with self.subTest(mode=mode):
                data = AuditedData()
                catalog, rows = trainer.load_training(mode, data=data)
                self.assertEqual(len(catalog), 8459)
                self.assertEqual(len(rows), expected)
                self.assertTrue(all(str(movie) in catalog and 0.5 <= rating <= 5.0
                                    for user, movie, rating in rows))
                self.assertEqual(data.training_called, mode == "evaluation")
                self.assertEqual(data.opened, ["training-catalog.csv", "evaluation/train.csv"]
                                 if mode == "evaluation" else ["training-catalog.csv"])

    def test_business_reader_refuses_unsealed_rating_file(self):
        trainer = self.trainer()
        from recommender.data import FrozenData, ContractError
        self.assertTrue(hasattr(trainer, "load_training"), "尚未实现业务评分摘要检查")
        with tempfile.TemporaryDirectory() as temp:
            data = FrozenData()
            original = data.package
            # 仅测试恶意路径注入，绝不修改正式冻结文件。
            package = Path(temp)
            (package / "business").mkdir()
            (package / "business/history_ratings.csv").write_text("tampered", encoding="utf-8")
            (package / "training-catalog.csv").write_bytes((original / "training-catalog.csv").read_bytes())
            data.package = package
            with self.assertRaises(ContractError):
                trainer.load_training("business", data=data)

    def test_cli_requires_mode_and_keeps_frozen_als_defaults(self):
        trainer = self.trainer()
        self.assertTrue(hasattr(trainer, "build_parser"), "尚未实现独立训练 CLI")
        parser = trainer.build_parser()
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([])
        for mode in ("evaluation", "business"):
            args = parser.parse_args(["--mode", mode])
            self.assertEqual(args.mode, mode)
            self.assertEqual(args.rank, 24)
            self.assertEqual(args.master, "local[2]")
            self.assertEqual(args.output_root, Path("models"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--mode", "evaluation", "--rank", "999"])

    def test_real_spark_fit_matches_exported_dot_products(self):
        trainer = self.trainer()
        self.assertTrue(hasattr(trainer, "fit_als"), "尚未实现真实 Spark ALS.fit")
        import os
        if os.environ.get("CINELAB_SPARK_INTEGRATION") != "1":
            self.skipTest("仅在隔离 Java17/PySpark 训练环境中启用真实 Spark 微型矩阵测试")
        from pyspark.sql import SparkSession
        import numpy as np
        spark = (SparkSession.builder.master("local[2]").appName("cinelab-spark-export-test")
                 .config("spark.ui.enabled", "false").getOrCreate())
        try:
            rows = [(u, i, float((u + i) % 5 + 1)) for u in (1, 3, 10) for i in (1, 2, 20)]
            frame = spark.createDataFrame(rows, "userId int, movieId int, rating float")
            model, parameters = trainer.fit_als(frame, rank=24)
            self.assertFalse(parameters["implicitPrefs"])
            self.assertFalse(parameters["nonnegative"])
            self.assertEqual(parameters["maxIter"], 8)
            self.assertEqual(parameters["seed"], 42)
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "model.npz"
                trainer.export_factors(path, {"1", "2", "20"},
                                       [(r.id, r.features) for r in model.itemFactors.collect()],
                                       [(r.id, r.features) for r in model.userFactors.collect()], rank=24)
                with np.load(path, allow_pickle=False) as arrays:
                    prediction = model.transform(frame.filter("userId=3 AND movieId=20")).first().prediction
                    self.assertAlmostEqual(float(arrays["user_factors"][1] @ arrays["item_factors"][2]),
                                           prediction, places=5)
        finally:
            spark.stop()

    def test_trainer_refuses_overwriting_existing_mode_model(self):
        trainer = self.trainer()
        self.assertTrue(hasattr(trainer, "train"), "尚未实现完整训练流程")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "evaluation" / "als-v1"
            target.mkdir(parents=True)
            marker = target / "manifest.json"
            marker.write_text("do not overwrite", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                trainer.train("evaluation", output_root=Path(temp))
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
