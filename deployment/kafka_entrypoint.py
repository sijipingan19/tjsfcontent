"""由官方Kafka归档构建的课程单节点启动入口；不重置已有KRaft数据。"""
import os
from pathlib import Path
import subprocess

from .kafka_support import make_properties, format_command


def main(environment=None, *, config_path=Path('/tmp/cinelab-kafka.properties'),
         run=subprocess.run, execute=os.execv):
    environment = dict(os.environ if environment is None else environment)
    properties = make_properties(environment)
    logdir = Path(properties['log.dirs'])
    if ',' in str(logdir):
        raise ValueError('课程容器只允许一个独立Kafka数据目录')
    logdir.mkdir(parents=True, exist_ok=True)
    config_path = Path(config_path)
    config_path.write_text(''.join(f'{key}={value}\n' for key, value in sorted(properties.items())), encoding='utf-8')
    cluster_id = environment.get('CLUSTER_ID', 'MkU3OEVBNTcwNTJENDM2Qk')
    command = format_command(logdir, config_path, cluster_id)
    if command is not None:
        run(command, check=True)
    program = '/opt/kafka/bin/kafka-server-start.sh'
    execute(program, [program, str(config_path)])


if __name__ == '__main__':
    main()
