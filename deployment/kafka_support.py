"""官方Kafka归档校验与单节点课程容器配置，不涉及用户数据。"""
import re


def parse_sha512(text: str) -> str:
    value = re.sub(r'\s+', '', text.split(':', 1)[-1]).lower()
    if not re.fullmatch(r'[0-9a-f]{128}', value):
        raise ValueError('Apache SHA-512 文件不是完整128位十六进制摘要')
    return value


def make_properties(environment: dict[str, str]) -> dict[str, str]:
    values = {
        'node.id': '1', 'process.roles': 'broker,controller',
        'listeners': 'PLAINTEXT://:9092,CONTROLLER://:9093',
        'advertised.listeners': 'PLAINTEXT://kafka:9092',
        'controller.listener.names': 'CONTROLLER',
        'listener.security.protocol.map': 'CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT',
        'controller.quorum.voters': '1@kafka:9093',
        'inter.broker.listener.name': 'PLAINTEXT',
        'offsets.topic.replication.factor': '1',
        'transaction.state.log.replication.factor': '1',
        'transaction.state.log.min.isr': '1',
        'group.initial.rebalance.delay.ms': '0',
        'auto.create.topics.enable': 'true', 'num.partitions': '1',
        'log.dirs': '/var/lib/kafka/data',
    }
    options = {'HEAP_OPTS', 'JVM_PERFORMANCE_OPTS', 'JMX_OPTS', 'LOG4J_OPTS', 'OPTS', 'CLUSTER_ID'}
    for name, value in environment.items():
        if not name.startswith('KAFKA_') or name[6:] in options:
            continue
        if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
            raise ValueError('Kafka环境配置必须是单行文本')
        key = name[6:].lower().replace('_', '.')
        values[key] = value
    return values


def format_command(logdir, config_path, cluster_id: str):
    from pathlib import Path
    if not re.fullmatch(r'[A-Za-z0-9_-]{22}', cluster_id):
        raise ValueError('Kafka cluster ID格式非法')
    metadata = Path(logdir) / 'meta.properties'
    if metadata.exists():
        entries = dict(line.split('=', 1) for line in metadata.read_text(encoding='utf-8').splitlines()
                       if '=' in line and not line.lstrip().startswith('#'))
        if entries.get('cluster.id') != cluster_id:
            raise ValueError('已有Kafka数据卷的cluster.id不匹配；拒绝重置或重新格式化')
        return None
    return ['/opt/kafka/bin/kafka-storage.sh', 'format', '--ignore-formatted',
            '--cluster-id', cluster_id, '--config', str(config_path)]
