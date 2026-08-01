# 开发者文档

本文面向 MediaCMS 开发者，介绍 API、开发环境、前端构建、转码和测试流程。

## 系统架构

系统由 Django Web/API、React 前端、PostgreSQL、Redis、Celery Worker 以及 FFmpeg/Bento4 媒体处理工具组成。上传、转码、缩略图、字幕和搜索等流程由后端模型、视图、序列化器与异步任务协同完成。更完整的后端结构、数据模型和业务流程见[后端架构中文介绍](project_backend_overview.md)。

## API 文档

API 通过 Swagger 提供文档，访问 `http://your_installation/swagger`，示例见 [demo.mediacms.io/swagger](https://demo.mediacms.io/swagger/)。登录后可以执行需要认证的操作。

```python
import requests

auth = ('user', 'password')
upload_url = "https://domain/api/v1/media"
media_file = '/tmp/file.mp4'

requests.post(
    url=upload_url,
    files={'media_file': open(media_file, 'rb')},
    data={'title': '标题', 'description': '描述'},
    auth=auth,
)
```

## 贡献与 Docker 开发

提交 PR 前执行 `pre-commit install` 和 `pre-commit run --all`，并阅读[贡献者行为准则](../CODE_OF_CONDUCT.md)。

开发环境启动命令：

```bash
docker compose -f docker-compose-dev.yaml build
docker compose -f docker-compose-dev.yaml up
```

前端构建并同步静态文件：

```bash
docker compose -f docker-compose-dev.yaml exec -T frontend npm run dist
cp -r frontend/dist/static/* static/
```

后端代码变更后重启：`docker compose -f docker-compose-dev.yaml restart web`。

## 视频转码

长视频会被切成多个片段，为各启用的 `EncodeProfile` 创建 `Encode` 任务。Worker 转码成功后按分辨率合并片段生成可下载文件；随后为成功的 MP4 生成 HLS 分片，用于流式播放。

## 自动化测试

```bash
docker compose up
docker compose exec -T web pip install -r requirements-dev.txt
docker compose exec --env TESTING=True -T web pytest
docker compose exec --env TESTING=True -T web pytest --cov=. --cov-report=html
```

`TESTING=True` 会让 Django 在测试环境中执行 Celery 任务，避免依赖后台 Worker。

> 英文原文：[developers_docs.md](developers_docs.md)
