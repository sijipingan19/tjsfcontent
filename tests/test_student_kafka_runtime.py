"""Kafka容器辅助测试；测试数据不是实际消息或实验指标。"""
import importlib
import importlib.util
import unittest


class KafkaRuntimeTests(unittest.TestCase):
    def test_apache_sha512_grouped_checksum_is_parsed_and_validated(self):
        self.assertIsNotNone(importlib.util.find_spec('deployment.kafka_support'), '缺少官方归档校验辅助模块')
        module = importlib.import_module('deployment.kafka_support')
        sample = 'kafka_2.13-4.1.1.tgz: ' + ' '.join(['ABCDEF01'] * 16)
        self.assertEqual(module.parse_sha512(sample), 'abcdef01' * 16)
        with self.assertRaises(ValueError):
            module.parse_sha512('kafka.tgz: not-a-real-checksum')

    def test_single_node_properties_use_real_kraft_and_internal_listener(self):
        from deployment import kafka_support
        self.assertTrue(hasattr(kafka_support, 'make_properties'), '尚未实现真实KRaft配置')
        values = kafka_support.make_properties({
            'KAFKA_ADVERTISED_LISTENERS': 'PLAINTEXT://kafka:9092',
            'KAFKA_HEAP_OPTS': '-Xmx512m',
        })
        self.assertEqual(values['process.roles'], 'broker,controller')
        self.assertEqual(values['controller.quorum.voters'], '1@kafka:9093')
        self.assertEqual(values['offsets.topic.replication.factor'], '1')
        self.assertEqual(values['log.dirs'], '/var/lib/kafka/data')
        self.assertEqual(values['advertised.listeners'], 'PLAINTEXT://kafka:9092')
        self.assertNotIn('heap.opts', values)
        with self.assertRaises(ValueError):
            kafka_support.make_properties({'KAFKA_NODE_ID': '1\ninjected=value'})

    def test_existing_kraft_storage_is_never_reformatted(self):
        from deployment import kafka_support
        from pathlib import Path
        import tempfile
        self.assertTrue(hasattr(kafka_support, 'format_command'), '尚未实现保留已有Kafka存储的入口')
        cluster = 'MkU3OEVBNTcwNTJENDM2Qk'
        with tempfile.TemporaryDirectory() as temp:
            logdir = Path(temp)
            config = logdir / 'server.properties'
            command = kafka_support.format_command(logdir, config, cluster)
            self.assertIn('format', command)
            self.assertIn('--ignore-formatted', command)
            (logdir / 'meta.properties').write_text('cluster.id=' + cluster + '\n', encoding='utf-8')
            self.assertIsNone(kafka_support.format_command(logdir, config, cluster))
            with self.assertRaises(ValueError):
                kafka_support.format_command(logdir, config, 'DifferentClusterID____')

    def test_entrypoint_starts_broker_without_touching_existing_metadata(self):
        from pathlib import Path
        import tempfile
        self.assertIsNotNone(importlib.util.find_spec('deployment.kafka_entrypoint'), '缺少真实Kafka启动入口')
        module = importlib.import_module('deployment.kafka_entrypoint')
        cluster = 'MkU3OEVBNTcwNTJENDM2Qk'
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            metadata = directory / 'meta.properties'
            original = 'cluster.id=' + cluster + '\n'
            metadata.write_text(original, encoding='utf-8')
            commands, executions = [], []
            module.main({'KAFKA_LOG_DIRS': str(directory), 'CLUSTER_ID': cluster},
                        config_path=directory / 'server.properties',
                        run=lambda *args, **kwargs: commands.append(args),
                        execute=lambda *args: executions.append(args))
            self.assertEqual(commands, [])
            self.assertEqual(metadata.read_text(encoding='utf-8'), original)
            self.assertEqual(executions[0][0], '/opt/kafka/bin/kafka-server-start.sh')
            self.assertIn('process.roles=broker,controller', (directory / 'server.properties').read_text())


if __name__ == '__main__':
    unittest.main()
