# 播放列表超额阻断 Implementation Plan

> **For agentic workers:** 使用 `superpowers:executing-plans` 按任务执行；只有用户选择代理执行时才使用 `superpowers:subagent-driven-development`。下列复选框用于跟踪实施进度。

**Goal:** 所有页面中的媒体加入播放列表操作，在超过配置容量时弹窗说明原因并阻断整次添加，不再静默部分成功。

**Architecture:** 单条和批量接口共用容量校验及写入服务。前端提交前执行无写入预检查，实际写入时后端在事务内锁定目标 Playlist 并再次校验；两次检查返回相同的结构化超额信息，前端复用一个告警弹窗。

**Tech Stack:** Django、Django REST Framework、PostgreSQL、React 17、Flux、TypeScript、Jest。

**Spec:** [Issue #6](https://github.com/caoyjie/mediacms/issues/6)。本文补充实现决策；用户已批准 inline execution，功能已实现并部署到 localhost。

## 范围与约束

- `MAX_MEDIA_PER_PLAYLIST` 已调整为 100 并部署，本计划不再次修改上限。所有响应中的 limit 动态读取 Django settings。
- 覆盖媒体详情页单条添加、批量管理（含只选择一个媒体）、创建列表后走上述入口的添加。
- 任一目标列表超额时，整个添加请求零写入；不允许用户点击“仍然添加”。
- 用户权限保持现有规则；预检查和正式提交必须经过相同权限校验，不泄露无权限列表的容量信息。
- 重复媒体不新增、不占新增容量。已有关系以 media_id 判断，不得用 ordering 参与存在性判定。
- 不引入依赖，不更改数据库结构，不自动清理历史重复关系。容量按现有 PlaylistMedia 行数计算，与当前后端限额口径一致；新增媒体先去重并排除已有关系。
- 不以 `Playlist.media_count` 判断容量：该属性过滤 `listable=True`，会漏算隐藏或不可列出的媒体。
- 工作区已有 AI 功能等未提交改动，实施前记录基线，只暂存本功能文件或相关补丁，不能整体提交工作区。
- 用户随后授权按顺序 inline execution，本次已执行实现、构建、部署。

## 方案选择

1. 仅透传现有失败提示：改动少，但无法解决批量部分成功和并发突破上限，不采用。
2. 前端根据已加载数量检查：响应快，但缓存、可见性过滤及重复媒体会导致误判，不作为权威判断。
3. **采用：后端共享事务服务 + 无写入预检查 + 统一弹窗。** 多一次请求，但权限、数量和去重规则一致，正式写入仍能防止竞态。

## 接口与行为决策

现有 `PUT /api/v1/playlists/{token}` 的 `type=add` 和 `POST /api/v1/media/user/bulk_actions` 的 `action=add_to_playlist` 均新增可选 JSON 布尔字段 `dry_run`，默认 false。必须验证类型；字符串 `"false"` 等非法值返回 400，不能用 Python truthiness 解析。

- `dry_run=true`：只检查，不写入，允许时返回 HTTP 200，`{allowed: true, limit: N, playlists: [...]}`。
- 超额：预检查与实际提交都返回 HTTP 400，保留 `detail`，新增稳定错误码 `playlist_capacity_exceeded`。
- 实际成功：单条保留 HTTP 201，批量保留 HTTP 200 及原有 detail，补充 `added_count`（实际新增关系总数）、`playlists`（每个列表新增数量）。重复添加视为成功且 added_count=0。
- 超额响应示例：

```json
{
  "code": "playlist_capacity_exceeded",
  "detail": "Playlist capacity exceeded",
  "limit": 100,
  "playlists": [
    {"id": 12, "title": "课程", "current_count": 99,
     "requested_new_count": 2, "remaining_capacity": 1}
  ]
}
```

失败响应仅列出超额列表；预检查成功响应列出全部目标。剩余容量取 `max(limit-current_count, 0)`。历史列表已超过上限时，零新增请求仍为幂等成功，有新增则阻断。

共享服务建议放在 `files/playlist_operations.py`：

`add_media_to_playlists(*, playlist_ids: list[int], media_ids: list[int], dry_run: bool = False) -> dict`

输入为视图已校验权限的数据库主键。通过 `PlaylistCapacityExceeded(payload)` 抛出容量异常，由两个视图转换为相同 HTTP 响应。服务不接受未经授权的外部 ID。

事务顺序：开启 atomic → 按主键升序锁定并实际加载全部目标 Playlist → 一次读取已有关系 → 计算各列表新增集合 → 检查全部列表 → 任一超额抛出异常 → 否则创建全部新增关系。锁使用 `select_for_update()`，保持到事务结束。新 ordering 从现有最大值后递增，不能用行数代替最大排序值。dry_run 同样使用该计算路径，但不执行创建；锁不跨 HTTP 请求保留。

两个添加入口必须全部迁移到服务，否则行锁不能提供完整的并发保障。删除和排序不会增加条目数，不纳入新增容量事务重构。目标列表在校验后被删除时返回受控错误，不能静默少处理一个目标。

前端流程：用户提交 → 禁止重复点击 → dry_run → 如超额打开弹窗并结束 → 如允许执行正式添加 → 再次超额使用同一弹窗 → 只有实际成功才更新本地成员状态和成功通知。

弹窗标题“播放列表容量不足”，逐项显示列表名称、上限、已有条数、本次新增条数、剩余容量；说明“本次添加未执行，请减少选择或移除已有条目”。只有“返回调整”关闭按钮；保留原选择，恢复提交能力。支持键盘焦点、Escape、关闭后焦点恢复；批量管理弹窗背景在告警打开时不可交互。

批量管理当前先添加后移除：超额时不得继续执行移除。添加成功后移除失败属于另一个请求的失败，不得显示整体成功；不在本计划扩大成跨添加/移除接口的原子事务。新建空列表是独立动作，之后超额不会删除新建列表。

## Review Focus

- 列表中不可见媒体仍占容量：任务 1 覆盖，避免使用 media_count。
- 列表已有重复关系或排序空洞：任务 1 验证不再新增重复，排序从 max 后追加。
- 预检查后容量变化：任务 2、3 验证正式提交仍拒绝且显示弹窗。
- 反向顺序操作多个相同列表：任务 2 并发验证固定锁序、无死锁及零部分写入。
- 批量添加失败后误执行移除、关闭选择框或显示成功：任务 3 验证无后续副作用。

## Task 1：共享容量校验与事务写入

**Files:** 新建 `files/playlist_operations.py`、`tests/api/test_playlist_capacity.py`；读取 `files/models/playlist.py`。

**Interfaces:** 实现上述 `add_media_to_playlists` 与 `PlaylistCapacityExceeded`。返回 added_count、limit、playlists；预检查额外返回 allowed=true。

- [x] 用 Django TestCase 和 APIClient 既有测试风格建立媒体、列表 fixture，使用 bulk_create 避免上传/转码副作用。
- [x] 编写失败测试：99+1 成功到 100；99+2 抛异常且仍为 99；100+已有媒体新增为 0；重复输入只新增一次；多列表任一超额全部不写入；dry_run 不写入。
- [x] 补充测试：override_settings 上限为 2 时遵循 2；不可列出媒体计数；历史重复不被自动删除且不再插入重复；ordering 有空洞时按 max 追加；历史超限零新增幂等通过。
- [x] 运行测试确认缺失行为导致失败：`docker compose exec -T web python manage.py test tests.api.test_playlist_capacity --verbosity 2`。测试使用独立 test 数据库，不能往实际业务库生成边界数据。
- [x] 实现事务、固定锁序、去重、全部校验后再写入和结构化异常。
- [x] 重跑同一测试文件，全部通过后只提交本任务补丁。

## Task 2：单条、批量 API 接入与并发保障

**Files:** 修改 `files/views/playlists.py`、`files/views/media.py`；扩展 `tests/api/test_playlist_capacity.py`；新建 `tests/api/test_playlist_capacity_concurrency.py`。

**Interfaces:** 保留原权限入口与成功状态码；dry_run 和容量响应遵循上文契约。两个接口均调用任务 1 服务，删除原逐条检查/部分写入逻辑。

- [x] 编写两个 API 的参数化/子测试：超额 code 和各数量字段准确；单条成功 201、批量成功 200；dry_run 成功 200 且无写入；非法 dry_run 返回 400。
- [x] 补充鉴权回归：匿名、无权列表、私有媒体不能因预检查或新服务绕过现有权限；目标被删除返回受控失败。
- [x] 新增 PostgreSQL TransactionTestCase，两个独立数据库连接同步发起不同新媒体添加：从 99 开始应恰好一成功一超额、最终 100；同一媒体并发应仅一条关系。
- [x] 覆盖两个客户端反向排列多个列表 ID，设线程超时并检查无死锁；冲突请求没有部分写入。禁止使用 SQLite 或 mock 锁替代此项证据。
- [x] 运行以上两个文件确认新行为测试失败，然后接入服务并补充 Swagger dry_run / 400 错误契约。
- [x] 重跑两个文件，全部通过后只提交任务相关改动。并发测试必须实际在 PostgreSQL 执行，不能以 skipped 作为通过。

## Task 3：统一超额弹窗与两个添加流程

**Files:**
- 新建 `frontend/src/static/js/components/PlaylistCapacityDialog.tsx` 及同名 `.scss`。
- 新建 `frontend/src/static/js/utils/helpers/playlistCapacity.js`，统一识别结构化容量错误。
- 修改 `frontend/src/static/js/utils/stores/MediaPageStore.js`、`frontend/src/static/js/components/playlists-selection/PlaylistsSelection.jsx`、`frontend/src/static/js/components/BulkActionPlaylistModal.tsx`。
- 修改 `files/frontend_translations/en.py`、`zh_hans.py`、`zh_hant.py`；其他语言沿用既有回退机制。
- 新建 `frontend/src/__tests__/playlist-capacity.test.jsx`、`playlist-capacity-flows.test.jsx`；沿用已有 React 17/Jest 工具，不新增测试框架。

**Interfaces:** `parsePlaylistCapacityError(data) -> payload|null` 只识别稳定 code 并验证数组/数值结构；`PlaylistCapacityDialog({error, onClose})` 在 error=null 时不显示。Flux 失败事件传递后端 response.data，通用失败仍保留普通通知。

- [x] 编写失败测试：弹窗呈现多个列表和动态上限；关闭/键盘/焦点行为；非法错误体降级普通失败，不崩溃。
- [x] 编写流程测试：预检查超额不调用写接口；预检查允许但正式提交超额同样弹窗；成功才更新成员关系；点击期间禁用，结束后可重试。
- [x] 覆盖批量失败不调用移除、不调用 onSuccess/onCancel、保留选择；移除失败不报整体成功；新增为 0 的幂等成功不声称新增了媒体。
- [x] 执行 `npm --prefix frontend test -- --runInBand --runTestsByPath src/__tests__/playlist-capacity.test.jsx src/__tests__/playlist-capacity-flows.test.jsx` 确认失败；使用当前 Jest 配置配置所需静态资源 mock。
- [x] 实现弹窗和解析器，两个入口接入 dry_run / 正式请求，保留容量错误详情。单条失败不提前修改勾选状态；批量添加使用 JSON 响应检查实际结果。
- [x] 添加可翻译的完整模板文案，动态值使用占位替换；列表标题作为普通 React 文本，不能作为 HTML 注入。
- [x] 重跑定向测试并执行前端测试套件；检查创建空列表后的添加也走上述流程。仅提交本任务变更。

## Task 4：验收与发布

**Files:** 构建产生的本功能静态资源；本计划勾选状态。无需新迁移。

- [x] 联合运行后端两个测试文件及前端测试，运行 `git diff --check`；如已有无关失败，记录基线并区分新增失败。
- [x] 执行 `npm --prefix frontend run dist`，确认构建成功，记录源文件与生成资源差异。不能为此重新构建无关视频编辑器。
- [x] 在隔离测试环境验收 99+1、99+2、100+1、混合已有/新媒体、多列表超额、预检查后并发占满；同时检查页面弹窗与数据库零部分写入。
- [x] 发布时先记录可回滚配置/静态资源，按项目既有方式同步 `frontend/dist/static/` 到 `static/`，随后 `docker compose restart web`。发布授权与环境确认沿用执行时会话，不在计划阶段操作。
- [x] 验证容器内限额为 100、播放列表 API 可用；用可清理的验收数据确认两入口均显示弹窗且没有假成功。清理仅限本次验收数据。
- [ ] 如回归，恢复本次代码和构建产物后重启，保留已经生效的 100 上限及既有其他改动。
- [ ] 在用户授权更新 issue 时附测试和部署证据；功能完成且验收通过后再关闭 Issue #6。

## 计划自检

Issue 的单条/批量/去重/动态上限/并发/国际化要求均有对应任务。预检查仅改善交互，不承担最终一致性；最终一致性依赖所有正式添加入口共用同一锁协议。本计划不承诺阻止直接 SQL 或未来绕过服务的写入，也不把添加和移除两个独立请求描述成一个原子事务。

## 执行结果（2026-10-07）

- 分支：`fix/playlist-capacity-guard`；原工作区 AI 等改动保留，未纳入提交。
- 后端定向：20 tests passed，包含 PostgreSQL 并发、鉴权后目标删除竞态、中文翻译字典回归。
- 前端全量：45 suites / 313 tests passed；构建成功，仅既有 bundle-size 警告。
- 后端全量：本地既有 .venv 执行时 153 passed、1 failed（`tests/api/test_new_media.py::TestX::test_file_upload`：HLS 信息预期 13，实际 0；本地缺少配置路径下的 Bento4）。此后新增两项回归包含在上述 20 项定向测试内。Docker 默认发现另受缺少 faker 测试依赖阻断。未修改无关上传逻辑或安装依赖。
- 独立审查：发现并修复批量鉴权后重复查询丢失已删除目标的问题；回归先失败后通过。
- 浏览器：独立数据库 `playlist_capacity_e2e_20261007` 与 18080 服务；单条 100+1 阻断且保留父弹窗；批量 100/99 +2 列出两个超额目标且数据库保持 [100,99]；单条 99+1 成功变为 [100,100]；中文提示及关闭后焦点/勾选状态正常。
- 部署：仅同步 8 个变化的 js/css 构建资源，未覆盖无关 Fine Uploader 资源；重启 Web，HTTP API 200，实际限额 100；8 个线上静态资源与构建产物逐个 hash 核对一致。
- 回滚备份：`/tmp/playlist-capacity-static-backup`；仅复制文件内容，保留部署文件原有所有权/权限。
- Issue #6 保留打开状态，未推送分支或合并；条件式回滚无需执行。
- 额外发现：原有中文批量操作菜单若干翻译为空，英文模式可正常显示；本次新超额文案的简体/繁体均已补齐，未扩大修改既有翻译行为。
