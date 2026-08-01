# 管理员文档

本文介绍 MediaCMS 的部署、配置、内容审核、权限、身份认证和维护操作。

## 目录

- Docker 安装与部署
- 系统配置
- 管理页面和 Django Admin
- 发布流程、用户角色、分类和标签
- 视频转码与 Whisper 自动字幕
- 静态页面、Google Analytics 和 Cookie Consent
- 翻译、RBAC、SAML 与身份提供商
- 自定义 URL、文件类型和上传限制

## Docker 安装与部署

安装 Docker 和 Docker Compose 后，按仓库中的 Compose 文件启动服务。完整部署建议使用项目提供的 `docker-compose.yaml`；需要完整转码能力时可使用 `docker-compose.full.yaml`，或将 Celery Worker 镜像切换为 `mediacms/mediacms:full`。

更新前请备份数据库和媒体文件，再拉取新镜像并重新创建服务。升级大版本时应按照对应版本的迁移说明执行 Django migrations。

部署参数包括：`ENABLE_UWSGI`、`ENABLE_NGINX`、`ENABLE_CELERY_BEAT`、`ENABLE_CELERY_SHORT`、`ENABLE_CELERY_LONG` 和 `ENABLE_MIGRATIONS`。它们分别控制 Web 服务、反向代理、定时任务、短任务 Worker、长任务 Worker 和数据库迁移。

常见部署形态：本机 HTTP、Let's Encrypt HTTPS、8000 端口高级部署、外部反向代理，以及 Docker Swarm/Kubernetes 等可扩展部署。

## 常用配置

配置通常写在 `deploy/docker/local_settings.py` 或环境变量中，常见选项包括：

- 更换门户 Logo、全局标题和主题样式。
- 通过 `REGISTER_ALLOWED`、`UPLOAD_ALLOWED`、`GLOBAL_LOGIN_REQUIRED` 控制注册、上传和登录要求。
- 设置媒体默认流程为 `public`、`private` 或 `unlisted`。
- 通过 `CAN_LIKE_MEDIA`、`CAN_DISLIKE_MEDIA`、`CAN_REPORT_MEDIA`、`CAN_SHARE_MEDIA` 和下载配置控制操作按钮。
- 设置邮件服务、登录限流、邮箱验证、用户审批、通知类型和站点地图。
- 配置播放列表数量、媒体上传大小、评论长度和并行上传数。
- 设置允许的媒体文件类型，以及是否启用 Whisper 自动字幕。

新上传媒体建议默认为私有；对于组织内部平台，可开启全局登录要求并关闭公开注册。

## 管理和发布流程

Django Admin 用于管理用户、媒体、分类、标签、转码配置、身份提供商和权限。若启用审核流程，媒体需由 Editor、Manager 或 Admin 审核后才会公开。

## 用户角色与 RBAC

系统角色包括普通用户、高级用户、Editor、Manager 和 Admin。启用 `USE_RBAC` 后，可将分类关联 RBAC Group，再为用户分配 Member、Contributor 或 Manager 角色，从而继承分类内媒体的查看、编辑和管理权限。详细规则见[媒体权限文档](media_permissions.zh-CN.md)。

## 分类、标签、字幕和转码

分类和标签可以在管理后台维护。字幕支持多语言 `.srt`/`.vtt` 文件；转码配置定义编码器、分辨率、码率和优先级。长视频可分片转码，任务全部成功后生成可下载文件和 HLS 流。

## 添加静态侧栏页面

在 `templates/cms/` 创建 HTML，在 `static/css/` 创建样式，在模板中扩展页面头和内容块；然后在视图文件中添加 View，在 `files/urls.py` 注册 URL，最后把页面加入左侧菜单并重启 Web 服务。

## SAML 与身份提供商

可通过 Django Admin 添加 SAML 身份提供商、登录选项以及用户属性、系统角色和 RBAC 组映射。Microsoft Entra ID 的完整配置请参阅 [SAML/Entra ID 中文说明](saml_entraid_setup.zh-CN.md)。

## 翻译、Whisper 和其他维护项

默认语言、可用语言、翻译文件、视频帧精灵图间隔和 Whisper 模型均可按部署需求调整。修改前端资源后需要重新构建并复制到 Django 的 `static/` 目录。

> 英文原文：[admins_docs.md](admins_docs.md)
