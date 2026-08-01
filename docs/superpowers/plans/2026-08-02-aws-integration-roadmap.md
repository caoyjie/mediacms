# MediaCMS AWS Integration Implementation Roadmap

**日期：** 2026-08-02

**依据：** `docs/superpowers/specs/aws-backend-integration/`

## 目标

将已批准的十个设计模块拆成可独立评审、测试和回滚的实施计划。每个计划只能依赖前序计划已经交付的稳定接口；Cloudflare 外部配置门不阻塞其余开发。

## 计划序列

| 顺序 | 计划 | 独立交付物 | 外部阻塞 |
| ---: | --- | --- | --- |
| 1 | Domain foundation | Media兼容字段、Job/Attempt/Checkpoint、AssetVersion、FIFO Lease、唯一管理员 | 无 |
| 2 | AWS infrastructure | 独立S3/IAM/CloudFront/MediaConvert模板、CloudWatch、dev Stack | ACM自定义域名部分等待实际域名 |
| 3 | Browser ingestion | Multipart API、恢复对账、HLS ZIP对象清单和上传租约 | 无 |
| 4 | Processing orchestration | 来源检查点执行器、MediaConvert协调、原子激活、取消和cleanup | 依赖AWS dev Stack |
| 5 | YouTube and subtitles | yt-dlp、加密Cookie、三轨WebVTT、无字幕和Resume | 依赖S3与编排 |
| 6 | CloudFront playback | Cookie Bootstrap、续期、资源授权和播放进度API | 最终自定义域名E2E等待Cloudflare门 |
| 7 | Frontend ingestion and tasks | Add Media、UploadEngine、Task Center、历史汇总和通知 | API契约完成即可开发 |
| 8 | Player and responsive layout | HLS质量/字幕、断点续播、移动布局和全局进度旋转图标 | 最终HTTPS移动E2E等待Cloudflare门 |
| 9 | Production delivery | GHCR、受限Compose、迁移初始化、备份、回滚和资源门禁 | 生产维护窗口与Tunnel切换 |
| 10 | End-to-end acceptance | 全矩阵、故障恢复、成本/指标、观察期和旧资源清理审批 | 所有前序计划 |

## 跨计划门禁

- 每个计划使用测试驱动开发并以独立提交交付。
- 新增数据库表和外部对象必须具备幂等、恢复和精确清理语义。
- 大文件不经过Django或Cloudflare Tunnel；后端不执行本地视频转码。
- PostgreSQL是任务顺序和状态的权威；Redis不是状态源。
- AWS dev/prod资源、凭证、Cookie和清理范围必须隔离。
- 新前端资源和文案全部使用英语。
- Cloudflare配置前继续计划1–5以及计划6的默认CloudFront域名验证；不得把等待控制台配置当作开发阻塞。

## 当前执行入口

从 [`2026-08-02-domain-foundation.md`](2026-08-02-domain-foundation.md) 开始。该计划完成并通过空库迁移、模型约束和故障恢复测试后，才编写并执行 AWS infrastructure 计划。
