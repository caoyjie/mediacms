# MediaCMS Moodle 插件

本文介绍在 Moodle 中配置 MediaCMS LTI 1.3 工具、安装插件以及教师和学生的使用方式。

## 管理员配置

### 1. 配置 MediaCMS

在 `local_settings.py` 中配置 MediaCMS 的 LTI issuer、密钥和相关站点参数。确保实例可通过 HTTPS 访问，并启用 LTI 1.3。

### 2. 在 Moodle 中添加外部工具

创建 LTI 1.3 外部工具，主要参数如下：

- 工具名称：MediaCMS。
- 工具 URL：MediaCMS issuer + `/lti/launch/`。
- 公钥集：MediaCMS issuer + `/lti/jwks/`。
- 初始化登录 URL：MediaCMS issuer + `/lti/oidc/login/`。
- 重定向 URI：MediaCMS issuer + `/lti/launch/`。
- 默认启动容器：嵌入，并启用 Deep Linking。
- 启用 NRPS（名称和角色服务）以及工具设置服务。
- 始终向工具共享启动者姓名和邮箱；成绩服务按 Deep Linking 定义或教师委派配置。

### 3. 安装 Moodle 插件

插件包含：`filter_mediacms`（提供公共配置和安全嵌入）与 `tiny_mediacms`（TinyMCE 插入按钮，依赖前者）。请先安装 `filter_mediacms`，再安装 `tiny_mediacms`。

安装后在 Moodle 后台启用 MediaCMS 过滤器并置于过滤器列表前部，在 TinyMCE 中配置默认嵌入选项。

### 4. 在 MediaCMS 中添加 Moodle 平台

在 MediaCMS Django Admin 添加 LTI Platform，填写 Moodle issuer、Client ID、登录 URL、Token URL、Audience、证书地址和 Deployment ID，并启用 NRPS、Deep Linking。可按需启用“用户退课时从组中移除”。

## 教师使用

教师可以在 My Media 中上传或录制媒体，编辑元数据、裁剪视频、生成或上传字幕、创建章节、替换媒体，并将媒体发布到课程或以不公开列出方式分享。也可以按用户或课程共享媒体，并在 Moodle 活动、资源或 TinyMCE 中嵌入媒体。

## 学生使用

学生通过课程中的嵌入媒体观看内容；其默认权限通常为 Viewer。教师或课程权限可以进一步决定是否允许下载、评论、收藏和其他操作。

## 权限映射

常见 Moodle 角色映射为：Student → Viewer，Teacher → Manager。课程共享、媒体发布和 TinyMCE 插入操作会根据课程成员关系同步 MediaCMS 权限。

> 英文原文：[moodle_plugin.md](moodle_plugin.md)
