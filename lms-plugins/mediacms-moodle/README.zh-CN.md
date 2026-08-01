# MediaCMS Moodle 插件

本目录包含 MediaCMS 的 Moodle 插件，用于通过 LTI 1.3 在 Moodle 中安全嵌入 MediaCMS 媒体。

## 安装顺序

| 顺序 | 插件包 | 插件 | 说明 |
| --- | --- | --- | --- |
| 1 | `filter_mediacms-v1.0.0.zip` | `filter_mediacms` | 必须先安装，提供公共配置 |
| 2 | `tiny_mediacms-v1.0.0.zip` | `tiny_mediacms` | 依赖 `filter_mediacms` |

可在 Moodle 后台 **Site Administration → Install plugins** 上传 ZIP；也可以解压到 Moodle 对应插件目录后执行升级。安装后在过滤器管理中启用 MediaCMS，并在 TinyMCE 中配置默认嵌入选项。

## 配置

在核心设置中填写 MediaCMS URL（例如 `https://lti.mediacms.io`）并选择已配置的 MediaCMS 外部工具。教师可以选择是否显示视频标题、标题链接、相关视频和用户头像。

## 使用方式

教师可以在 My Media 中上传或管理媒体、裁剪视频、处理字幕、创建章节，并将媒体共享到课程或嵌入课程资源。学生通常以 Viewer 权限观看课程媒体。

## 开发与排障

插件源代码分别位于 `filter_mediacms` 和 `tiny_mediacms` 目录。出现嵌入失败时，先确认 MediaCMS URL、LTI 1.3 外部工具、Client ID、Deployment ID、HTTPS 和过滤器启用状态；再检查 Moodle 和 MediaCMS 的日志。

> 英文原文：[README.md](README.md)
