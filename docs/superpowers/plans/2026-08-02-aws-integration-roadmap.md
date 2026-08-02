# MediaCMS AWS Integration Implementation Roadmap

**日期：** 2026-08-02

**依据：** `docs/superpowers/specs/aws-backend-integration/`

## 目标

将已批准的十个设计模块拆成可独立评审、测试和回滚的实施计划。每个计划只能依赖前序计划已经交付的稳定接口；Cloudflare 外部配置门不阻塞其余开发。

## 计划序列

| 顺序 | 计划 | 独立交付物 | 外部阻塞 |
| ---: | --- | --- | --- |
| 1 | ✅ Domain foundation（完成） | Media兼容字段、Job/Attempt/Checkpoint、AssetVersion、FIFO Lease、唯一管理员 | 无 |
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
- 任何会联网下载大量依赖、工具、包、容器镜像或浏览器运行时的操作，Codex只提供完整命令、预期下载内容、磁盘影响和验证命令，等待管理员手动执行；不得自行执行。已安装依赖的离线检查、lint和测试不受此限制。
- AWS部署命令显式使用`aws --profile default --region us-east-1`。资源创建、更新和删除只通过CloudFormation Stack/Change Set执行；不得用`aws s3api create-bucket`、`aws iam create-*`、`aws cloudfront create-*`或`aws mediaconvert create-job-template`绕过Stack。
- CloudFormation变更先执行`cfn-lint`和`aws cloudformation validate-template`，再创建并审阅Change Set；IAM资源必须显式使用`CAPABILITY_NAMED_IAM`。删除Stack、清空Bucket和旧资源清理仍需单独批准。
- AWS CLI的`default` profile只用于管理员部署与只读验收，不复制到镜像或容器。生产运行时凭证由AWS infrastructure计划定义的独立最小权限身份提供。

## AWS参考实现边界

参考文件为`/home/caoyujie/projects/cyj/media-platform/infra/aws/media-platform.yaml`。只迁移经过MediaCMS需求复核的结构，不复制物理名称或既有资源引用：

- 复用设计模式：S3 Block Public Access/加密/版本控制/Multipart生命周期、CloudFront OAC、Public Key/Key Group双钥轮换、credentialed CORS response policy、ACM可外部DNS验证、MediaConvert Service Role、CloudFront SourceArn约束的Bucket Policy以及非秘密Outputs。
- 必须改造：Bucket默认名改为`mediacms-${AWS::AccountId}-us-east-1`；Tags改为`Project=mediacms`并包含Environment；来源前缀、CORS应用域名、生命周期、CloudWatch和MediaConvert Job Templates按本项目规范参数化。
- 禁止照搬：Vercel custom origin/default behavior、`media-platform-*`名称、Route 53自动记录（DNS由Cloudflare管理）、现有Bucket/Distribution/Role/Public Key以及任何旧AWS资源。
- 参考模板中的IAM User、AccessKey和Secrets Manager静态凭证组合不自动视为MediaCMS最终方案。AWS infrastructure计划必须比较生产宿主机可行的最小权限运行时凭证方案，并确保CloudFormation输出不包含SecretAccessKey。

## 当前执行入口

Domain foundation 已通过空库迁移、模型约束、故障恢复和 legacy pipeline guard 验证。下一步编写并执行 AWS infrastructure 计划。
