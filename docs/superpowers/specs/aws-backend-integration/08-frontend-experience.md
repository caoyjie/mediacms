# 08. 前端资源整合与体验

**日期：** 2026-08-01

**状态：** 已确认设计，待实施

## 1. 范围

本模块权威定义 AWS 模式下的前端组件边界、统一添加媒体向导、浏览器上传客户端、全局任务中心、处理图标、播放器客户端和断点续播。上传安全与服务端协议以 `03` 为准，编排状态以 `04` 为准，CloudFront 授权契约以 `06` 为准。

目标是保留现有 MediaCMS Header、Sidebar、媒体列表、详情、编辑页和 Video.js 体验，只替换旧上传内核并增加 AWS 状态能力。

## 2. 整合策略

采用现有 React 17 页面体系内的增量扩展：

- 保留当前 renderer、Portal、页面配置、SCSS 主题和元数据 CRUD。
- 新增 TypeScript React 组件替换添加媒体页对 Fine Uploader 的依赖。
- 在根 Provider 中增加媒体授权和任务 Provider；普通页面导航重新加载后，从服务端和 IndexedDB 重建任务并自动续传。
- `frontend-tools/video-js` 继续作为独立 React 19/Video.js 8 构建，通过 API 和 Cookie 与主站协作，不跨 React Root 共享 Context。
- 不建立第二个媒体管理 SPA，不迁移现有列表和编辑页。
- 添加媒体页停止加载 Fine Uploader 5.13；首期不删除仓库库文件，先审计其他引用。

```mermaid
flowchart TB
    ROOT[React 17 App Root]
    ROOT --> LAYOUT[LayoutProvider]
    LAYOUT --> THEME[ThemeProvider]
    THEME --> USER[UserProvider]
    USER --> AUTH[MediaAuthorizationProvider]
    AUTH --> TASK[MediaTaskProvider]

    TASK --> HEADER[Header Portal与任务图标]
    TASK --> PAGE[当前页面]
    TASK --> DRAWER[全局任务抽屉]
    PAGE --> WIZARD[AddMediaWizard]
    PAGE --> CRUD[现有列表与编辑]
    CRUD --> PLAYER[独立Video.js构建]
```

## 3. 组件与服务边界

### 3.1 MediaAuthorizationProvider

- 进入含受保护媒体的列表、详情、编辑或播放页时调用 Cookie Bootstrap。
- 记录服务端提供的过期时间，临近过期单飞续期。
- 并发授权型 403 共享一次刷新；刷新失败不无限循环。
- 对外只暴露 `ensureAuthorized`、`refreshAuthorization` 和安全状态，不暴露 Cookie 值。

### 3.2 MediaTaskProvider

- 获取当前任务、队列位置、最近历史和汇总摘要。
- 协调服务端唯一上传租约，并使用 Web Locks/BroadcastChannel 防止同一浏览器多个标签页竞争。
- 协调 IndexedDB 恢复、页面刷新对账和任务抽屉。
- Provider 不持有 File 大对象；文件句柄/指纹和上传恢复数据由持久化层管理。

### 3.3 AddMediaWizard

- 负责来源选择、文件/URL输入、核心元数据、预检查和任务创建。
- 创建 Job 后将生命周期交给 Provider；同页操作不会终止上传，普通页面导航后由新页面自动恢复。
- 向导草稿可持久化，但不得保存 Cookie、预签名 URL 或媒体完整内容。

### 3.4 UploadEngine

- 独立 TypeScript 服务，不依赖 React，可由 Provider 驱动和单元测试。
- 使用 XHR PUT 上传 S3 Part，以获得字节进度和立即 abort 能力。
- 负责暂停、继续、URL续签、重试、ETag、服务端同步和严格单任务；只有持有服务端上传租约时才能发起 PUT。
- 对浏览器只提供状态事件；Django/S3 对账结果始终覆盖本地推断。

### 3.5 TaskCenter

- 抽屉显示当前任务、队列和最近历史；完整页面显示长期历史、Attempt 和汇总。
- 只消费稳定任务 API，不自行推导后端检查点是否完成。

### 3.6 PlaybackProgressClient

- 位于独立播放器构建内，查询、提示、保存和离线同步播放位置。
- 通过播放会话时间戳/版本避免旧标签页覆盖新进度。
- 不改变现有 `MediaAction(action=watch)` 的观看计数语义。

## 4. 统一添加媒体向导

统一“添加媒体”页面采用四步向导：

```mermaid
flowchart LR
    S1[1 选择来源] --> S2[2 文件或URL]
    S2 --> S3[3 核心元数据]
    S3 --> S4[4 确认并创建]
    S4 --> TC[全局任务中心]
```

来源固定为：

1. 本地视频或音频。
2. 本地 HLS ZIP。
3. YouTube 单视频。

字幕上传仍在媒体编辑页，不作为独立媒体来源。

### 4.1 来源步骤

- 本地媒体：只选择一个文件，显示类型、大小、快速指纹和可恢复会话。
- HLS ZIP：显示扫描状态、入口 manifest、文件数量、解压总量和安全检查。
- YouTube：输入并规范化单视频 URL，显示 Cookie 最近上传日期、无历史警告和发现结果。

### 4.2 核心元数据

向导只录入标题、描述、标签、分类、可见性和可选封面。自动值标记来源；管理员修改后标为人工值，后端自动探测不得覆盖。高级字段、字幕和完整 CRUD 继续使用现有媒体编辑页。

### 4.3 确认步骤

显示来源、文件、预计上传量、元数据、字幕发现和 Cookie 使用状态。确认后立即创建 Media 草稿、Job 和上传会话。管理员可以前往媒体编辑页或留在任务详情；全局任务中心继续工作。

### 4.4 Cookie 组件

同一安全组件出现在：

- YouTube 向导快捷入口。
- 管理设置页。
- Cookie 导致失败的任务 Resume 区域。

只显示最近上传和最近使用日期，永不显示或下载明文。存在有效历史版本时默认使用最新版本；没有时显示警告但允许无 Cookie 尝试。

## 5. 前端依赖与资源

| 能力 | 选型 | 约束 |
| --- | --- | --- |
| HLS ZIP | `@zip.js/zip.js` | Web Streams、Worker、Zip64；Worker数量固定1 |
| IndexedDB | `idb` | 保存恢复元数据，不保存媒体或秘密 |
| Multipart | 自建 UploadEngine + XHR | 浏览器无 AWS 凭证；Django签发短期URL |
| 状态 | React Context + reducer | 单管理员、单上传，不引入Redux |
| 播放 | 现有 Video.js 8 | 继续独立构建 |
| 图标 | Material Icons + CSS | 遵循现有主题和减少动画偏好 |

不引入浏览器 AWS SDK。普通 API 延用 Axios；XHR 只承担需要上传进度的 S3 PUT。

```mermaid
flowchart LR
    FILE[File或ZIP Entry Stream] --> ENGINE[UploadEngine]
    ENGINE --> API[Django签发Part URL]
    API --> ENGINE
    ENGINE -->|XHR PUT| S3[S3]
    S3 -->|ETag| ENGINE
    ENGINE --> IDB[(IndexedDB)]
    ENGINE -->|批量同步Part| API
```

ZIP entry 通过 Stream 进入受控 Part 缓冲区，不生成完整解压 Blob。依赖依据：[zip.js](https://github.com/gildas-lormeau/zip.js)、[idb](https://github.com/jakearchibald/idb)、[S3 Multipart](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)。

## 6. 严格单上传与恢复

前端上传也严格单任务。新任务可创建，但只有队首能取得服务端上传租约。Django租约是跨浏览器和跨设备的权威保护；Web Locks API和BroadcastChannel只是同一浏览器内的快速协调与状态广播。租约包含owner token、heartbeat和过期时间，页面崩溃后必须等待过期并与S3对账再接管。

```mermaid
stateDiagram-v2
    [*] --> waiting_upload
    waiting_upload --> uploading: 获得前端上传租约
    uploading --> paused: 管理员暂停
    paused --> uploading: 继续
    uploading --> waiting_processing: S3来源验证完成
    waiting_processing --> processing: 获得后端租约
    processing --> completed
    uploading --> canceled
    processing --> failed
```

- 支持 File System Access API 时，在明确授权后保存文件句柄。
- 不支持或权限失效时，要求重新选择同一文件。
- 文件名、大小、lastModified 和快速指纹必须匹配；不匹配禁止复用 Part。
- HLS ZIP 重新选择后跳过已由 S3验证的 entry/Part。
- IndexedDB 保存业务会话 ID、Upload ID、文件指纹、Part/ETag、HLS entry映射和向导草稿；不持久化预签名 URL。
- 刷新恢复先查询 Django/ListParts/Object 验证，再更新 IndexedDB。
- 普通页面导航会中止当前XHR；已完成Part保留，未完成Part在新页面重传。新页面初始化Provider后重新取得/接管租约并自动续传，因此不承诺单个XHR跨页面存活。
- 关闭浏览器、失去文件句柄权限或换设备后，任务保持 `action_required`；重新授权同一文件后继续。

## 7. 全局任务中心与长期历史

Header 按钮打开任务抽屉；抽屉显示当前任务、等待队列和最近历史，“查看全部”进入完整页面。

```mermaid
flowchart TB
    ICON[Header任务状态] --> DRAWER[任务抽屉]
    DRAWER --> ACTIVE[当前任务]
    DRAWER --> QUEUED[等待上传/等待处理]
    DRAWER --> RECENT[最近历史]
    RECENT --> FULL[完整任务中心]
    FULL --> SUMMARY[长期汇总]
    FULL --> FILTERS[时间/来源/状态/媒体]
    FULL --> ATTEMPTS[Job与Attempt]
```

活动任务卡必须显示：媒体标题、来源、当前阶段、阶段/总体两条进度、字节或文件数、速度、剩余时间、队列位置、最近更新时间、允许操作和安全错误。

历史任务、Attempt、诊断和汇总长期保留，不自动删除。媒体删除后保留标题快照和审计关系。完整页展示成功率、总上传量、处理时长、来源分布、失败分类和 AWS 输出分钟数；诊断默认折叠且已脱敏。

## 8. 旋转任务图标

Header 在存在未终结任务时显示任务状态图标：

- `waiting_upload/waiting_processing`：静态时钟和等待数量徽标。
- 上传、验证、下载、转码、发布、清理：`sync/autorenew` 外圈以 `1.1s linear` 旋转，细进度环显示总体进度。
- 点击打开任务抽屉，悬停显示当前阶段和百分比。
- 全部成功：显示勾选约 1.5 秒后淡出并从 DOM 移除。
- 失败或等待 Cookie/文件授权：停止旋转并显示警告，直到管理员确认或处理。
- 历史完成任务不重新显示图标。
- `prefers-reduced-motion` 时不旋转，只更新静态进度环、图标和可读文本。

```mermaid
stateDiagram-v2
    [*] --> hidden
    hidden --> queued: 存在等待任务
    queued --> spinning: 开始上传或处理
    spinning --> queued: 当前完成且仍有等待
    spinning --> success: 全部成功
    success --> hidden: 1.5秒后
    spinning --> warning: 失败或等待操作
    warning --> queued: 已恢复且需等待
    warning --> spinning: 已恢复并立即执行
```

## 9. 播放器客户端

媒体 API 向播放器提供 `media_id`、`asset_version_id`、`media_type`、稳定 CloudFront manifest/poster/thumbnail URL、duration、实际字幕轨和处理状态。

- 初始化前确保 CloudFront Cookie 已 Bootstrap。
- HLS 默认 Auto；手动清晰度偏好保存在浏览器。
- 新媒体不存在偏好档位时选择最接近且不高于偏好的档位；清晰度菜单以 manifest 为准。
- 手动档位不因网络自动降级；提示可切回 Auto。
- 字幕只显示实际存在的中文、English、中文 / English；无字幕显示“字幕暂无可用选项”。
- 音频使用同一授权、字幕和进度逻辑，隐藏清晰度菜单并显示封面。
- 授权型 403 触发一次单飞刷新；图片加一次 cache-busting，HLS重载；仍失败则停止循环并显示登录/权限错误。

## 10. 断点续播

新增独立 `PlaybackProgress`，不复用 `MediaAction.extra_info`：

```text
PlaybackProgress
- administrator_id
- media_id
- asset_version_id
- position_seconds
- duration_seconds
- completed
- playback_session_version
- last_played_at
```

唯一约束为管理员和 Media。`asset_version_id` 用于判断替换语义，不把每个资源版本产生为独立永久进度。

```mermaid
flowchart LR
    PLAYER[Video.js] -->|节流保存| API[PlaybackProgress API]
    API --> DB[(PlaybackProgress)]
    PLAYER -->|首次有效播放| WATCH[现有watch action]
    DB --> CONTINUE[继续观看]
    DB --> PROMPT[断点恢复提示]
```

### 10.1 恢复规则

- 小于 10 秒：从头，不提示。
- 10 秒至 95%且距结尾至少 30 秒：显示“从 mm:ss 继续 / 从头播放”。
- 默认选择继续，但不强制自动播放。
- 超过 95%或距结尾不足30秒：视为已完成，下次从头。
- URL显式 `?t=` 优先，不显示断点提示。
- 音频与视频规则一致。

### 10.2 保存与冲突

- 播放中每10秒节流保存；pause、seeked、页面隐藏和销毁立即保存。
- ended 设置 `completed=true`；进度更新不增加观看次数。
- 网络失败只在本地保留最新待同步位置；联网后提交最新值。
- 请求携带播放会话版本/时间戳，服务端拒绝旧标签页覆盖更新进度。
- 重新编码同一内容默认保留进度；替换源文件默认重置，管理员可明确选择保留。
- 媒体卡片显示细进度条，并提供“继续观看”列表。

## 11. 错误与恢复契约

API 错误使用稳定 `error_code + safe_message + allowed_actions`，前端禁止解析错误字符串决定流程。

- 网络上传失败指数退避，默认最多5次；已由 S3确认的 Part不重传。
- 预签名 URL过期透明续签，不计普通上传失败次数。
- 文件权限丢失进入 `action_required`，保留已上传 Part。
- HLS ZIP安全或 manifest失败立即停止后续上传并展示可理解的问题。
- Cookie失败提供内嵌上传和 Resume。
- 断点保存失败不打断播放，本地排队并非阻塞提示。
- 取消二次确认，说明上传数据将由后端最终清理。
- 日志、UI、IndexedDB和分析事件均不得包含 Cookie、签名 URL、CloudFront签名、本地完整路径或秘密。

## 12. 测试与验收

### 12.1 单元

- reducer、队列和唯一上传租约。
- Part暂停、重试、续签、取消和ETag合并。
- 文件指纹、句柄恢复、HLS路径/压缩限制。
- 进度聚合、旋转图标状态和减少动画。
- 断点阈值、完成判定、版本替换和旧会话冲突。

### 12.2 API契约

- 上传创建、Part URL、完成、恢复、取消和S3对账。
- 当前任务、长期历史、汇总和Attempt。
- Cookie状态、上传和Resume。
- CloudFront Bootstrap/续期。
- PlaybackProgress查询、写入、完成和版本替换。

### 12.3 浏览器端到端

- 视频/音频/HLS ZIP的暂停、刷新、重新授权、恢复和完成。
- 第二任务严格等待，第一任务暂停/结束后才获得上传租约。
- 同页离开向导时上传不中断；跨页面导航后自动重建并续传，Header图标与任务中心一致。
- 多标签页、不同浏览器或设备同时打开时，服务端租约仍只允许一个上传者。
- 成功图标消失，失败警告保留。
- YouTube无Cookie、默认历史Cookie和Cookie失败Resume。
- Cookie过期后缩略图、poster、WebVTT和HLS恢复。
- 清晰度Auto/手动记忆、字幕三轨/单轨/无字幕。
- 视频/音频断点、`?t=`优先、已完成重播和跨设备冲突。

### 12.4 非功能

- 多GB文件和ZIP entry不整体进入内存。
- 键盘可操作向导、抽屉、恢复提示和播放器菜单。
- 进度条具备可访问数值与文本；窄屏抽屉为全屏面板。
- 真实浏览器连接测试S3 Multipart、CORS、ETag和CloudFront Cookie；Mock不能代替该验收。
