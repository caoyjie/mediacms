# 10. 测试环境与资源受限生产部署

**日期：** 2026-08-01

**状态：** 已确认设计，待实施计划

## 1. 范围与依据

本模块依据仓库根目录的 `arch-test-environment.txt` 和 `ubuntu-production-environment.txt`，权威定义 AWS 模式的测试分层、机器职责、生产容量准入、GHCR 镜像交付、维护清理、首次部署、回滚和发布门禁。功能验收仍以 `07-deployment-and-acceptance.md` 为准；本模块解决“在哪里、以多少资源、按什么顺序执行”。

全局原则：Arch 是完整开发测试环境；Ubuntu 只运行轻量控制面；S3、MediaConvert 和 CloudFront 承担大文件存储、重转码与分发；生产不构建镜像、不运行压力测试。

## 2. 实测容量与职责

### 2.1 Arch 测试机

实测14个逻辑CPU、30 GiB内存、约20 GiB可用内存、59 GiB Swap、约491 GiB可用NVMe，并具有Docker、AWS CLI、FFmpeg、yt-dlp、GUI和Intel Arc硬件能力。

承担：

- Django/PostgreSQL/Redis/Celery完整开发栈。
- 前端构建、真实浏览器和移动设备联调。
- 视频、音频、HLS ZIP、YouTube和字幕测试。
- CloudFormation与AWS dev资源部署。
- 故障注入、恢复、完整发布候选验收。
- GHCR镜像构建；生产机只拉取不可变镜像。

本地测试最多使用8个CPU、16 GiB容器内存，并始终保留100 GiB磁盘余量。媒体闭环严格串行，普通单元测试可以并行。报告显示项目目录曾达到约53 GiB，而生产仓库约157 MiB；这是镜像构建前的阻塞异常。必须按目录盘点`.git`、本地媒体、缓存、下载、bind数据和测试输出，并审计Docker build context和`.dockerignore`。构建上下文不得包含媒体、环境报告、Cookie、`.env`、缓存、数据库或Git对象。

### 2.2 Ubuntu 生产机

实测2 vCPU、3.8 GiB内存、约730 MiB可用、3.8 GiB Swap且已使用1.5 GiB、磁盘仅余12 GiB且使用率89%。Docker已有13个运行容器，镜像占31.48 GiB并有约16.59 GiB可回收；Journal占3.5 GiB；systemd为degraded，且存在多次OOM记录。

该机器不可升级，但允许在30–60分钟维护窗口迁移/清理现有容器。清理前禁止部署。上线硬门槛：

| 项目 | 门槛 |
| --- | ---: |
| 拉取镜像前可用磁盘 | 至少35 GiB |
| 部署完成空闲可用磁盘 | 至少28 GiB |
| 磁盘使用率 | 不高于75% |
| 空闲时可用内存 | 稳定至少1.5 GiB |
| 空闲时Swap使用 | 低于1 GiB |
| systemd | 非degraded，或每个残留失败单元有无影响说明 |
| OOM | 维护期及部署期无新增OOM |
| 端口 | 8000/8080/5432/6379冲突已解除或改为内部网络 |

任何门槛未通过必须中止部署，不以增加Swap掩盖持续内存不足。

两台报告的可选网络测试均被跳过。进入AWS集成前，Arch必须验证浏览器到S3、CloudFront、AWS API和GHCR push；生产必须验证GHCR pull、S3 HEAD/PUT、MediaConvert/CloudWatch API、Cloudflare Tunnel以及一个短YouTube metadata/download。网络测试失败属于部署阻塞，不以短视频规避不可达问题。

## 3. 测试分层

```mermaid
flowchart TD
    CODE[Feature branch] --> UNIT[Unit and contract tests]
    UNIT --> ARCH[Arch integration environment]
    ARCH --> BROWSER[Desktop and mobile browser E2E]
    ARCH --> AWS[AWS dev resources]
    AWS --> FAILURE[Recovery and failure tests]
    FAILURE --> IMAGE[Build immutable GHCR image]
    IMAGE --> PROD[Ubuntu production smoke test]
    PROD --> RELEASE[Enable production traffic]
```

### 3.1 Arch单元、契约与本地集成

- Django模型、三套状态、检查点、权限和单管理员。
- Multipart、Task Action、PlaybackProgress、capabilities和资源版本API契约。
- reducer、UploadEngine、IndexedDB迁移、TaskView解析、轮询和多标签租约。
- Video.js字幕、清晰度、Cookie续期、断点续播与移动控件。
- CloudFormation lint/template和Docker Compose健康。
- Mock只验证业务分支，不能替代真实S3/MediaConvert/CloudFront验收。

### 3.2 AWS dev集成

- 浏览器Multipart直传、暂停、刷新、重选文件和续传。
- 视频固定ABR + QVBR、单音频HLS、HLS ZIP和首帧回退。
- Signed Cookie Bootstrap、过期与恢复。
- 中文、英文、双语和无字幕。
- candidate完整验证与active原子切换。
- MediaConvert幂等、失败、取消与清理。
- dev/prod使用不同Stack、Tags、Bucket或强隔离前缀、CloudFront Distribution和环境化模板；dev Cookie不能授权prod，dev cleanup不能触及prod。
- 删除dev Stack时验证生产Bucket、Distribution和模板不受影响；AWS Budget和告警在测试前启用。

普通提交只跑一条短视频闭环；完整MediaConvert矩阵每天或发布前运行。测试资产建立版本化manifest，记录名称、来源、许可、SHA-256、时长、分辨率、字幕和预期结果。自有20–60秒360p、1–3分钟720p、30–60秒音频、小型HLS ZIP和SRT/VTT是稳定fixture。YouTube同时维护主/备两个公开短视频；外部视频删除、限流或字幕变化归类为fixture failure，不直接判代码失败。还需覆盖仅英文、无字幕和必要Cookie场景。

### 3.3 浏览器与移动

- Arch真实Chrome/Firefox；Safari使用真实Apple设备。
- 多标签上传租约、自适应轮询和CloudFront授权恢复。
- Android Chrome与iPhone Safari的后台、锁屏、网络切换、页面回收。
- safe-area、`100dvh`、软键盘、横竖屏、44×44px控件。
- 旋转不得自动全屏；字幕、质量与Resume必须可达且不互相遮挡。

### 3.4 Ubuntu生产冒烟

生产只执行容器健康、迁移、单管理员、capabilities、AWS健康、一个最短视频直传/MediaConvert/播放/删除清理、Tunnel外部访问和资源观察。禁止并发、压力、大文件极限、Docker build和完整浏览器矩阵。

## 4. GHCR镜像交付

- Arch或GitHub Actions构建镜像。
- 标签同时包含commit SHA和发布版本，例如`sha-<commit>`与`aws-mvp-v1`。
- 生产Compose固定镜像digest或SHA标签，不使用`latest`。
- 生产使用只读GHCR Token，仅执行pull。
- Web与Worker首期共用一个镜像，以不同命令运行；前端静态资源在构建时进入镜像。
- 保留当前和上一版本用于回滚，其余版本按明确清单定向清理。
- 发布清单记录commit、镜像digest、数据库迁移版本、CloudFormation版本和MediaConvert TemplateVersion。

生产不得复用仓库根`docker-compose.yaml`。新增独立`deploy/compose/docker-compose.aws-production.yml`；现有Compose只作为开发基线。生产Compose必须：固定GHCR digest、不bind mount源码、使用独立命名volume/network、Web只绑定`127.0.0.1`、DB/Redis不发布宿主机端口、无默认密码、设置healthcheck/restart/log rotation/PID和cgroup资源限制，并为临时目录与持久数据提供独立挂载。

生产镜像必须在Arch以与生产相同的`linux/amd64`平台、Compose和配置进行资源复现：限制2 CPU、3.8 GiB总内存，依次测量空闲30分钟、migration、短上传协调、yt-dlp、字幕合并、单帧、MediaConvert轮询、cleanup、pg_dump/restore。所有服务的limit在该测试后固化，不能仅依据表格估算。

## 5. 生产拓扑与资源预算

```mermaid
flowchart LR
    ADMIN[Admin browser] --> TUNNEL[Cloudflare Tunnel]
    TUNNEL --> NGINX[Web容器内Nginx]
    NGINX --> WEB[Gunicorn/Django]
    WEB --> DB[(PostgreSQL)]
    WEB --> REDIS[(Redis)]
    REDIS --> WORKER[Celery Worker concurrency=1]
    BEAT[Celery Beat] --> REDIS
    ADMIN -->|Multipart| S3[(Private S3)]
    WORKER --> TEMP[Bounded temp storage]
    WORKER --> S3
    WORKER --> MC[MediaConvert]
    MC --> S3
    ADMIN --> CF[CloudFront] --> S3
```

| 服务 | CPU上限 | 内存上限 | 初始策略 |
| --- | ---: | ---: | --- |
| web | 0.60 | 640 MiB | Supervisor + Nginx + Gunicorn 1 worker、2–4 threads |
| celery_worker | 0.70 | 768 MiB | concurrency=1 |
| celery_beat | 0.10 | 128 MiB | 仅轻量调度 |
| db | 0.45 | 640 MiB | PostgreSQL小内存配置 |
| redis | 0.10 | 128 MiB | 仅Broker |
| 合计上限 | 1.95 | 2304 MiB | 为OS、Docker、Tunnel留余量 |

表中数值只是Arch限额试验起点，不是未经测量即可上线的最终值。每个服务要同时记录idle、p95和peak RSS，持续超过limit的80%必须重新测量/调参。当前Gunicorn实际配置为2 workers，生产配置必须显式覆盖为1 worker；Celery Beat、父/子Worker和Web内全部进程均计入对应容器上限。

PostgreSQL初始参数：`max_connections=20`、`shared_buffers=128MB`、`effective_cache_size=512MB`、`maintenance_work_mem=64MB`、`work_mem=4MB`、`wal_buffers=8MB`。必须实测migration、连接峰值、并发`work_mem`、索引、autovacuum和pg_dump/restore；测试证明需要后才增加连接数。Redis使用`maxmemory 96mb`、`maxmemory-policy noeviction`、`appendonly no`，任务权威数据仍在PostgreSQL。

Celery固定`concurrency=1`、`prefetch_multiplier=1`、`worker_max_tasks_per_child=10`，并将子进程内存阈值初始设为约500 MiB。实际参数需在Arch测量后固化；Worker退出/重启必须从数据库检查点恢复。

`worker_max_memory_per_child`单位按Celery要求使用KiB，且只在当前任务结束后替换子进程，不能充当峰值硬限制。必须同时依靠Docker内存限制、任务输入上限和实测。Redis无AOF意味着Broker重启可丢消息，因此reconciler必须从PostgreSQL重建queued任务，审计running任务和MediaConvert提交意图，再幂等重新入队。清空/重启Redis的故障测试必须证明无任务永久丢失、无重复MediaConvert提交且仍严格单任务。

## 6. 临时文件与轻任务

- 临时目录固定为`/var/lib/mediacms/tmp`，不放在项目目录。
- 每个Attempt独立、可解析的子目录；删除只能针对已解析且与终态Attempt匹配的精确目录。
- 本地视频与HLS直传S3，不进入后端临时目录。
- YouTube启动前执行磁盘准入检查：可用空间必须至少为`max(12 GiB, 预计下载量 × 1.5 + 8 GiB)`，任意时刻8 GiB为硬下限。
- MVP单个YouTube下载默认上限4 GiB；先用metadata/format size估算，大小未知或空间不足时拒绝启动。
- 移动或失败不能绕过限制；成功、失败、取消均进入cleanup。
- 服务启动可扫描遗留目录，但不得按时间或宽泛路径直接递归删除。
- yt-dlp、字幕和必要ffmpeg位于Worker镜像，宿主机无需安装Node、浏览器或ffmpeg。

## 7. Tunnel、安全与网络边界

- 沿用宿主机systemd cloudflared，origin指向明确的`127.0.0.1` Web容器Nginx端口；切换前备份并验证现有ingress，不能影响共享Tunnel上的其他服务。
- PostgreSQL和Redis只存在于Docker内部网络，不映射公网。
- S3、MediaConvert、CloudFront不通过Tunnel。
- 现有8000/8080冲突在维护盘点后选择新loopback端口，不抢占未知服务。
- CloudFront Cookie Domain、Secure、HttpOnly和SameSite按生产域名配置。
- 生产Secret不进入Git、镜像、Compose渲染日志或环境报告；Secret文件权限`0600`，数据库使用随机强密码，GHCR Token仅`read:packages`。
- Web不得以root运行；Worker仅对精确临时/日志目录可写。逐服务验证`no-new-privileges`、`cap_drop: ALL`和只读root filesystem；确需写入的路径使用命名volume/tmpfs并记录例外。
- 镜像扫描确认不含`.env`、cookies.txt、AWS credential、CloudFront私钥或本地媒体。

## 8. 维护、备份与定向清理

```mermaid
flowchart TD
    INVENTORY[Inventory] --> BACKUP[Backup required data]
    BACKUP --> STOP[Stop selected services]
    STOP --> CLEAN[Targeted cleanup]
    CLEAN --> GATE{Resource gate passed?}
    GATE -->|No| ABORT[Abort]
    GATE -->|Yes| DEPLOY[Deploy immutable image]
```

维护前使用专用只读盘点脚本记录容器名称、Compose project、镜像/digest、CPU/RSS、端口、挂载volume、restart policy、容器与volume/image引用关系、失败systemd单元、Journal分类占用、内存、Swap和OOM。脚本禁止输出容器环境变量、Secret或Token。未形成“保留/迁移/删除”精确清单前，不授权任何清理。

备份需覆盖要迁移服务的数据、Tunnel配置、Compose/环境配置、systemd override、防火墙及当前镜像digest；数据库备份必须恢复抽检。MediaCMS上线后PostgreSQL至少每日备份，至少一份加密异机或S3备份，目标RPO 24小时、RTO 2小时，每月至少恢复演练一次。恢复后验证唯一管理员、active asset version和Job checkpoint；数据库备份与S3媒体保护分别管理。

只有在单独确认精确对象后才能停止/删除已迁移容器、无用镜像、build cache、归档Journal和已备份无主卷。禁止宽泛`docker system prune -a`，禁止删除运行服务资源，禁止在此阶段清理旧AWS资源。

## 9. 首次部署与回滚

```mermaid
flowchart TD
    PULL[Pull and verify digest] --> DATA[Start DB and Redis]
    DATA --> MIGRATE[Run migrations]
    MIGRATE --> ADMIN[Create singleton admin]
    ADMIN --> APP[Start Web Worker Beat]
    APP --> HEALTH[Internal health]
    HEALTH --> TUNNEL[Switch Tunnel]
    TUNNEL --> SMOKE[Production smoke]
    SMOKE -->|Pass| RELEASE[Release]
    SMOKE -->|Fail| ROLLBACK[Rollback image/Tunnel]
```

部署顺序：拉取固定digest → 创建专用network/volumes → DB/Redis健康 → 一次性migration → 初始化唯一管理员 → Web内部健康 → Worker/Beat且只有一个slot → Tunnel切换 → 最短视频冒烟 → 开放Add Media。

应用回滚时先切维护页或上一origin，停止新Web/Worker/Beat，切回上一digest并只读健康检查。不得自动反向迁移数据库。首次迁移失败且尚无正式数据时可在明确确认后重建本次专用数据库卷；已有正式数据后只允许前向修复或经验证备份恢复。candidate保持不可见，active资源不得因应用回滚删除。

## 10. 上线观察

首次上线后至少观察60分钟，并满足全部量化门槛：

| 指标 | 通过条件 |
| --- | ---: |
| 宿主/容器OOMKilled | 0 |
| 容器异常重启 | 0 |
| Host MemAvailable | 始终至少512 MiB |
| Swap增长 | 60分钟不超过256 MiB |
| 单容器RSS | 不持续超过limit的80% |
| 可用磁盘 | 至少28 GiB |
| 临时目录 | 终态后回到基线+100 MiB以内 |
| PostgreSQL连接 | 不超过20 |
| Redis内存 | 不超过80 MiB |
| 冒烟后队列 | 0 |
| Django/Cloudflare 5xx | 0 |
| Worker内存替换 | 0 |

同时观察MediaConvert、请求延迟、cleanup failure和Tunnel。任一门槛失败、资源持续恶化或新增OOM必须回滚，不能继续上传测试媒体。

## 11. 分阶段门禁

1. **Phase 0资源治理：** 完成脱敏盘点、保留/迁移/删除清单、备份抽检、网络预检和端口确认，并达到分阶段生产硬门槛。
2. **Phase 1本地基础：** 全新DB、单管理员、领域模型、FIFO、Multipart、授权、Add Media、Task Center；单元/契约/Compose通过。
3. **Phase 2 AWS媒体：** 短视频、720p、音频、HLS ZIP、视觉资源、字幕；直传、QVBR、原子激活和清理通过。
4. **Phase 3 YouTube：** 公开短视频、英文/无字幕、可选Cookie失败Resume；临时文件与磁盘限制通过。
5. **Phase 4播放与移动：** 质量、三轨、Cookie续期、断点、版本切换和真实移动设备通过。
6. **Phase 5故障恢复：** Web/Worker/Redis重启与Redis清空重建、URL/Cookie过期、AWS失败、S3不一致、cleanup与低磁盘；检查点、队列重建、幂等和active保护通过。
7. **Phase 6候选镜像：** 完整测试、GHCR SHA/digest、使用同一镜像在Arch复验、记录版本清单。
8. **Phase 7生产：** 维护、pull、全新DB、Tunnel、最短冒烟、60分钟观察；禁止压力与构建。
9. **Phase 8稳定期：** 审查成功率、成本、峰值和备份恢复；旧AWS资源必须再次取得明确批准后才清理。

## 12. 发布门禁

- 单元、契约、Arch Compose与至少一次AWS短视频完整闭环通过。
- 上传恢复、Cookie续期、无本地转码回退、candidate隔离通过。
- 新增界面英语和移动关键路径通过。
- GHCR镜像固定SHA/digest，上一版本可回滚。
- 生产专用Compose、容器安全、Secret扫描和cgroup限制验证通过。
- Arch使用生产镜像完成2 CPU/3.8 GiB受限资源复现，所有服务限额有测量证据。
- 固定fixture manifest、主/备YouTube和AWS dev/prod隔离通过。
- 生产资源准入全部通过。
- 冒烟期间无OOM，Swap不持续增长，临时目录和测试资源已清理。
- 任一关键门禁失败则停止，不降低验收标准强行上线。

## 13. 实施依据

- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/) 与 [Compose deploy resources](https://docs.docker.com/reference/compose-file/deploy/)：容器默认无资源限制，生产必须显式配置并验证cgroup。
- [Celery configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html)：prefetch与`worker_max_memory_per_child`的单位和任务结束后替换语义。
- [PostgreSQL resource consumption](https://www.postgresql.org/docs/current/runtime-config-resource.html)：`shared_buffers`、`work_mem`等内存参数必须结合容器上限和并发实测。
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)：生产按digest拉取不可变镜像。
