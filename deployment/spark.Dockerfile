# 独立训练容器：整个项目 bind 到 /app，不连接数据库、不公开端口。
# 项目原 .dockerignore 不放行训练依赖文件，用最小 tar context，无需改动既有部署规则：
# tar -cf - deployment/spark.Dockerfile training-requirements.txt | docker build --pull=false -f deployment/spark.Dockerfile -t cinelab-spark:3.5.7 -
# docker run --rm --network none --hostname localhost --mount "type=bind,source=<项目绝对路径>,target=/app" cinelab-spark:3.5.7 --mode evaluation
# 业务模式必须另启进程：把上一行 --mode 改为 business。
# 优先使用已校验导入的本地 Python3.12 基础层，避免公共仓库拉取阻塞。
# 公网可用时也可 --build-arg TRAINING_BASE=python:3.11.14-slim-bookworm。
ARG TRAINING_BASE=cinelab-offline/api:1.3.0-a1
FROM ${TRAINING_BASE}
USER root

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
RUN python -c "from pathlib import Path; p=Path('/etc/apt/sources.list.d/debian.sources'); s=p.read_text(); s=s.replace('http://deb.debian.org/debian-security', '${DEBIAN_MIRROR}/debian-security').replace('http://deb.debian.org/debian', '${DEBIAN_MIRROR}/debian'); p.write_text(s)" \
    && apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    PATH=/opt/cinelab-training/bin:$PATH \
    PYSPARK_PYTHON=/opt/cinelab-training/bin/python \
    PYSPARK_DRIVER_PYTHON=/opt/cinelab-training/bin/python \
    SPARK_LOCAL_IP=127.0.0.1 \
    SPARK_LOCAL_HOSTNAME=localhost \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

COPY training-requirements.txt /opt/cinelab-training-requirements.txt
RUN /usr/local/bin/python -m venv /opt/cinelab-training \
    && python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r /opt/cinelab-training-requirements.txt \
    && python -c "import numpy,pyspark,pydantic; print('training packages:',numpy.__version__,pyspark.__version__,pydantic.__version__)" \
    && java -version

WORKDIR /app
ENTRYPOINT ["python", "-m", "recommender.student.train_als"]
