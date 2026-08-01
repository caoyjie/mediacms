# MediaCMS Web 客户端（演示）

## 环境要求

- Node.js 版本不低于 14.17.0。

## 安装与开发

```bash
npm install
npm run start
```

浏览器访问 [http://localhost:8088](http://localhost:8088)。

## 构建

```bash
npm run dist
```

构建结果位于 `frontend/dist/`，将 `frontend/dist/static/` 中的文件复制到项目的 `static/` 目录，供 Django 服务使用。

## 测试脚本

- `npm run test`：运行一次全部单元测试。
- `npm run test-watch`：以监听模式运行测试。
- `npm run test-coverage`：生成 `./coverage` 覆盖率报告。
- `npm run test-coverage-watch`：以监听模式运行覆盖率测试。

> 英文原文：[README.md](README.md)
