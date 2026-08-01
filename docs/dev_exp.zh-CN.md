# 开发者体验

本文介绍使用 Docker 本地开发 MediaCMS 的基本流程。

## 使用 Docker 本地开发

先安装较新版本的 [Docker](https://docs.docker.com/get-docker/) 和 [Docker Compose](https://docs.docker.com/compose/install/)，然后执行：

```bash
docker compose -f docker-compose-dev.yaml up
```

几分钟后访问 `http://localhost`，默认登录信息为 `admin/admin`；React 开发服务器位于 `http://localhost:8088/`。

开发 Compose 会构建后端镜像 `mediacms/mediacms-dev:latest` 和前端镜像，并启动 Django、React、PostgreSQL、Redis 以及 Celery 服务。Django 运行在 Debug 模式下，使用 `runserver`，不启动 Gunicorn/Nginx；静态文件来自 `static/`，并允许跨域请求。

## Django 开发

Django 会自动重载 Python 代码。若编辑过程中出现语法错误导致服务停止，可以重启 Web 容器：

```bash
docker compose -f docker-compose-dev.yaml restart web
```

## React 开发

代码位于 `frontend/`，开发服务器端口为 8088。React 作为 Django 模板加载的前端库使用，并不是独立 SPA；重点关注 `frontend/src` 和 `templates/`。完成改动后构建并复制静态文件：

```bash
docker compose -f docker-compose-dev.yaml exec frontend npm run dist
cp -r frontend/dist/static/* static/
docker compose -f docker-compose-dev.yaml restart web
```

## 常用命令

```bash
make admin-shell
make build-frontend
```

> 英文原文：[dev_exp.md](dev_exp.md)
