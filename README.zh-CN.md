# MediaCMS（中文介绍）

MediaCMS 是现代化、功能完整的开源视频与媒体内容管理系统（CMS），面向媒体观看、管理和分享场景。它可以在几分钟内搭建中小型视频门户。

项目主要采用 Django + React 技术栈，并提供 REST API。在线演示：[demo.mediacms.io](https://demo.mediacms.io)。

## 主要功能

- 数据完全自主掌控，可自行部署和托管。
- 基于 Django/Python/Celery、React 的现代技术栈。
- 支持公开、私有、不公开列出等发布流程。
- 支持基于角色的访问控制（RBAC），可按分类和用户组管理查看、编辑权限。
- 可集成本地运行的 Whisper，实现自动转录。
- 支持视频、音频、图片和 PDF 等媒体类型。
- 支持分类、标签和自定义媒体归类。
- 支持社交分享和视频嵌入代码生成。
- 支持 LTI 1.3 和 Moodle 插件，可用于学习管理系统（LMS）。
- 支持视频剪辑、分段、替换和另存为新媒体。
- 支持 SAML 单点登录及角色、用户组映射。
- 提供实时搜索、播放列表、字幕/CC、响应式设计、深色主题和可配置用户注册策略。
- 支持多种转码配置（H.264、H.265、VP9）和 HLS 自适应流媒体。
- 支持可暂停、可恢复的分片上传、Swagger API 文档和多语言界面。

## 适用场景

- 高校、学校和教育机构的视频教学平台。
- 不适合上传到外部网站的组织内部或敏感媒体。
- 面向社区的视频门户和媒体共享平台。
- 个人媒体库和内容归档门户。

## 设计理念

MediaCMS 希望提供高质量的开源 Web 应用，用于建设社区门户并支持协作。项目目标是提供现代系统应有的功能、降低安装维护成本，并方便定制和扩展。

## 许可证与服务

MediaCMS 使用 [GNU Affero General Public License v3.0](LICENSE.txt) 发布。项目提供定制安装、功能开发、系统迁移、旧系统集成、培训和技术支持，详见[服务页面](https://mediacms.io/#services/)。

## 硬件建议

中小型部署（每天上传数小时视频、数百名活跃用户）建议至少使用 4 GB 内存和 2–4 个 CPU。更大规模的部署应增加 CPU 和内存。

磁盘空间可按预期上传视频总量的约三倍估算，因为系统会保留原始文件、转码文件和 HLS 文件。例如每天上传 1 GB 视频并保留一年，可按约 1 TB 磁盘规划。启用 Whisper 自动转录时，建议额外配置更多 CPU。

## 安装与文档

MediaCMS 支持 Docker Compose，也支持通过自动化脚本在服务器上安装所需服务。

- [管理员文档](docs/admins_docs.md)
- [用户文档](docs/user_docs.md)
- [开发者文档](docs/developers_docs.md)
- [开发环境说明](docs/dev_exp.md)
- [媒体权限](docs/media_permissions.md)
- [转码说明](docs/transcoding.md)
- [Moodle 插件](docs/moodle_plugin.md)
- [后端架构与业务模型中文介绍](docs/project_backend_overview.md)

## 技术栈

Python、Django、Django REST Framework、Celery、PostgreSQL、Redis、Nginx、Gunicorn、React、Fine Uploader、video.js、FFmpeg、Bento4。

## 参与贡献

可以通过提交 Issue、参与 Discussions、报告问题、提出功能建议、编写文章、改进文档、提交 Pull Request 或修复缺陷来参与项目。

## 联系方式

info@mediacms.io

> 英文原文：[README.md](README.md)
