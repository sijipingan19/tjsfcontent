"""标准版学生部署；不修改教师部署文件、凭据或已有业务卷。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import json
import re
import subprocess


GUARD_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. (Join-Path $env:CINELAB_STUDENT_ROOT 'deployment/offline/runtime.ps1')
Read-CourseRelease
Open-CourseRuntime -RequireSaved
Assert-CourseEngine
Assert-CourseImages
Assert-CourseConfiguration
Assert-CourseOwnership
$result = [ordered]@{project=$script:Runtime.project;context=$script:Runtime.context;
    port=$script:Runtime.port;secretDir=$script:Runtime.secretDir;engineId=$script:EngineId}
Write-Output ('CINELAB_RUNTIME=' + ($result | ConvertTo-Json -Compress))
'''


def assert_no_links(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError('构建输入越出项目目录') from exc
    for cursor in (path, *path.parents):
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('不支持符号链接/junction/reparse 构建输入')
        if cursor == root:
            break


BLOCKED = {'private', 'data', 'models', 'offline-images', 'node_modules', '__pycache__', '.git'}


def allowed_name(name: str) -> bool:
    return name not in BLOCKED and not name.startswith(('.venv', '.env'))


def tree_files(root: Path, relative: str, suffix: str | None = None):
    base = root / relative
    assert_no_links(base, root)
    for directory, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(name for name in dirs if allowed_name(name))
        for name in dirs:
            assert_no_links(Path(directory) / name, root)
        for name in sorted(files):
            if allowed_name(name) and (suffix is None or name.endswith(suffix)):
                yield (Path(directory) / name).relative_to(root).as_posix()


def copy_build_context(root: Path, context: Path, services: tuple[str, ...]) -> list[str]:
    """只复制白名单；不遍历任何 private/data/models/node_modules 目录。"""
    if not services or len(set(services)) != len(services) or set(services) - {'api', 'web'}:
        raise ValueError('仅支持 api/web 构建目标')
    if not str(context).isascii():
        raise ValueError('BuildKit context 必须使用 ASCII 路径')
    if context.exists() and any(context.iterdir()):
        raise ValueError('构建目标目录必须为空，禁止覆盖已有内容')
    if 'web' in services and not (root / 'frontend/dist/index.html').is_file():
        raise ValueError('frontend/dist/index.html 不存在；请先执行真实 npm ci/build')
    names: set[str] = set()
    if 'api' in services:
        for directory in ('backend', 'recommender'):
            names.update(tree_files(root, directory, '.py'))
        names.update(p.relative_to(root).as_posix() for p in (root / 'backend/migrations').glob('*.sql'))
        names.update(p.relative_to(root).as_posix() for p in (root / 'deployment').glob('*.py'))
        names.update(('backend/requirements.lock.txt', 'deployment/requirements.lock.txt',
                      'deployment/student-backend.Dockerfile', 'scripts/student_package.py'))
    if 'web' in services:
        names.update(tree_files(root, 'frontend/dist'))
        names.update(('deployment/student-frontend.Dockerfile', 'deployment/nginx.conf'))
    for relative in sorted(names):
        assert_no_links(root / relative, root)
    context.mkdir(parents=True, exist_ok=True)
    for relative in sorted(names):
        destination = context / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, destination)
    (context / '.dockerignore').write_text('# 此目录已按白名单隔离；不继承排除 dist 的教师规则。\n', encoding='utf-8')
    return sorted(names | {'.dockerignore'})


def validate_development_state(state, project: str, engine_id: str, inspect_image) -> dict:
    """先校验标识格式，再查询镜像；任何不匹配均拒绝，不修复旧状态。"""
    import re
    if (not isinstance(state, dict) or state.get('schema') != 'cinelab-development-v1'
            or state.get('project') != project or state.get('engineId') != engine_id
            or not isinstance(state.get('images'), list) or not state['images']):
        raise ValueError('开发状态身份不匹配；保留状态文件并检查原项目/Engine')
    images = {}
    for entry in state['images']:
        if not isinstance(entry, dict):
            raise ValueError('开发镜像条目无效')
        service = entry.get('service')
        if service not in ('api', 'web') or service in images:
            raise ValueError('开发服务必须为不重复的 api/web')
        prefix = f'cinelab-student/{project}-{service}:'
        reference, image_id = entry.get('reference'), entry.get('id')
        if (not isinstance(reference, str)
                or not re.fullmatch(re.escape(prefix) + '[a-f0-9]{32}', reference)
                or not isinstance(image_id, str) or not re.fullmatch('sha256:[a-f0-9]{64}', image_id)):
            raise ValueError('开发镜像引用/id 格式不合法')
        images[service] = dict(entry)
    for entry in images.values():
        actual = inspect_image(entry['reference'])
        if (not isinstance(actual, dict) or actual.get('Id') != entry['id']
                or actual.get('Os') != 'linux' or actual.get('Architecture') != 'amd64'):
            raise ValueError('开发镜像不存在、id 已变化或不是 Linux/amd64；禁止替换状态')
    return images
