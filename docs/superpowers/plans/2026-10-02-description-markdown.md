# Media Description Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or, if selected by the user, superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 仅为媒体播放页的 `description` 增加安全 Markdown 展示，保留原文编辑及时间戳跳转。

**Architecture:** 使用 markdown-it 解析 description，在独立 React 组件内将受支持的 token 转为 React 元素；只在非链接的普通文本中生成时间戳锚点。后端保存 Markdown 原文，在 HTML 和 JSON 输出边界分别转义，不保存渲染结果。

**Tech Stack:** 现有 React 17、Webpack、Jest/jsdom、Django/DRF、pytest；新增 markdown-it（14.x，最低 14.1.0，npm lock 固定实际版本）。不升级 React 或测试框架。

**Spec:** [GitHub issue #4](https://github.com/caoyjie/mediacms/issues/4) 及本会话确认的设计。用户明确范围仅限 `description`；验收仅限桌面浏览器，无手机浏览器或原生 App 验收。以下 Global Constraints 是执行时使用的完整范围约束。

## Global Constraints

- 支持标题、粗体、斜体、有序/无序列表、链接、引用、行内代码、围栏代码块。
- 普通文本可读，段落内单换行保留；不承诺空行数量逐字符映射到视觉高度。
- `01:30` 和 `01:02:30` 可点击跳转；代码和链接内部不转换时间戳。
- 原始 HTML 作为文本展示，不执行；危险链接协议不得成为可点击链接。
- 数据库及编辑入口保存原始 Markdown；不增加字段、迁移或历史数据回填。过去已被 strip_tags 删除的内容无法恢复。
- 不新增 summary，不改变其现存兼容分支；当前后端没有 summary 字段。
- 不修改评论、播放器越界时间戳策略；不增加编辑器、预览、语法高亮或 Markdown 图片加载。
- 仅调整 description 的必要输出边界，不借此重构整页元数据。
- 保留现有未提交修改：deploy/docker/local_settings.py、docker-compose.yaml、files/views/media_auth.py、tests/api/test_media_auth.py 及其他已有文档。

## Review Focus

1. 时间戳在链接的强调文本、代码和自动链接中出现时不能嵌套锚点（Task 1）。
2. HTML 实体、大小写或控制字符伪装的危险协议不能绕过链接校验（Task 1）。
3. Markdown 前导缩进及结尾空格经 API/表单编辑后不能被 trim 破坏（Task 2）。
4. 描述中的换行、反斜杠及 `</script>` 不能破坏 JSON-LD 或退出脚本元素（Task 2）。
5. 长链接、长代码和嵌套列表在桌面窗口不得撑宽页面；已有摘要兼容分支不能影响实际无摘要页面（Task 3）。

## 技术选择与代码依据

- ViewerInfoContent.js 当前先对整段字符串替换时间戳，再 dangerouslySetInnerHTML；替换为组件调用，删除此局部拼接逻辑。
- VideoJSEmbed.jsx 的 document 点击监听读取 `.video-timestamp` 和 `data-timestamp`；生成锚点保留此协议，锚点内部使用纯文本。
- Media.save() 当前 strip_tags 同时处理 title 和 description；仅 description 改为保留原文。
- MediaMetadataForm 使用 textarea；检查并关闭 description 的表单 strip。MediaSerializer 的描述输入同样显式关闭 trim_whitespace。
- MediaPage.scss 的父容器有 white-space: pre-wrap；描述容器改 normal，换行由 Markdown softbreak 输出 br，pre/code 保留代码空白。
- templates/cms/media.html 在多种媒体类型的 JSON-LD 内直接插值 description；原文包含换行会使 JSON 无效，需要 JSON 字符串序列化。
- 选 markdown-it 是为了沿用现有 React/Jest 工具链，并直接区分文本、代码和链接 token。使用官方公开 parse 接口，不自行实现 Markdown 语法。
- npm 是 frontend/README.md 和 CI 实际使用的包管理器；更新 frontend/package.json、frontend/package-lock.json，不顺带重写历史 yarn.lock。

参考：[token 结构](https://markdown-it.github.io/markdown-it/documents/Architecture.html)、[解析和链接校验 API](https://markdown-it.github.io/markdown-it/interfaces/MarkdownIt.html)、[14.1.0 CJS/ESM 入口](https://github.com/markdown-it/markdown-it/blob/14.1.0/package.json)。实施安装时检查实际解析版本及安全公告，若存在适用漏洞则采用修复版并记录兼容性结果。

## Task 1: 安全 Markdown 与时间戳组件

**Files:**
- Create: frontend/src/static/js/components/media-page/MediaDescription.jsx
- Create: frontend/tests/media-page/MediaDescription.test.jsx
- Modify: frontend/package.json、frontend/package-lock.json

**Interfaces:**
- Produces: `MediaDescription({ source: string }): ReactElement | null`，根节点 className 为 media-content-description，空白输入返回 null；非空输入解析前不 trim。
- 内部 `renderTokens(tokens, keyPrefix, insideLink): ReactNode[]` 处理块及行内 token；`renderTimestampText(text, keyPrefix): ReactNode[]` 生成文本和时间戳链接，不作为公共 API。
- 锚点输出：`<a href="#" className="video-timestamp" data-timestamp="90">01:30</a>`。

- [ ] **1. 写失败测试。** 按现有 ReactDOM.render/act 模式创建组件测试，覆盖以下断言：

| 用例 | 必须验证 |
| --- | --- |
| `# 标题`、粗斜体、两种列表、引用、代码 | 对应 h1/strong/em/ol/ul/blockquote/code/pre 节点和内容正确；嵌套列表及非 1 起始有序列表正确 |
| `第一行\n第二行`、空串、纯空白 | 第一例有 br；后两例无描述 DOM |
| `01:30 与 01:02:30` | 恰有两个时间戳锚点，秒数分别为 90、3750 |
| 行内代码、围栏/缩进代码、`[**01:30**](https://example.com)` | 内容保留，无内部时间戳锚点 |
| 合法 https/http/mailto、相对路径和页内锚点 | 正常链接可用，标题属性为文本 |
| javascript/vbscript/file/data 链接及大小写、实体、控制字符变种 | 无危险 href；标签文字可读 |
| script、img onerror、svg/onload、伪造 data-timestamp HTML | 不生成这些 HTML 元素或事件属性，不生成攻击者指定的时间戳属性 |
| 中英文相邻时间戳、连续多个时间戳 | 无字符丢失；完整匹配 hh:mm:ss，不将其拆成两个锚点 |
| Markdown 图片、未闭合语法、转义标点 | 无图片网络请求；内容可读，无异常 |

- [ ] **2. 在 frontend 运行 `npm test -- --runInBand tests/media-page/MediaDescription.test.jsx`。** 预期因组件不存在失败，保留失败证据。
- [ ] **3. 安装 `markdown-it@^14.1.0` 并实现组件。** 配置 html:false、breaks:true、linkify:false、typographer:false；禁用 image/table/strikethrough 等未要求扩展。只映射受支持的 token 标签，不透传 token 任意属性，不使用 dangerouslySetInnerHTML；文本由 React 转义。白名单属性仅 href/title、有序列表 start 及本组件生成的时间戳属性。链接先沿用 parser 默认校验，再限定 http/https/mailto 或无协议的相对链接；不放宽 parser 默认安全检查。以 link 深度贯穿嵌套渲染，跳过链接、code_inline、fence、code_block 内时间戳。延续原匹配允许一至两位时/分、两位秒，优先完整长时间戳，不改变播放器越界处理。
- [ ] **4. 重跑上述测试。** 必须全部通过；新增一个包含所有必需格式的混合样例，断言渲染没有重复 key 警告。
- [ ] **5. 仅提交本任务文件。** 建议提交信息：`feat: add safe media description markdown component`。

## Task 2: description 原文存储与输出边界

**Files:**
- Modify: files/models/media.py、files/serializers.py、files/forms.py
- Modify: files/templatetags/custom_filters.py、templates/cms/media.html
- Create: tests/api/test_media_description.py
- Create: tests/forms/test_media_description.py
- Create: tests/pages/test_media_description.py

**Interfaces:**
- Consumes: 现有 Media.description、MediaSerializer、SingleMediaSerializer、MediaMetadataForm；字段名与 API 响应结构保持不变。
- Produces: 保存再读取 description 返回原文；API 更新仍使用 `PUT /api/v1/media/{friendly_token}`，当前成功状态为 201。
- Produces: 模板过滤器 `json_string(value: str) -> SafeString`：使用 json.dumps 序列化为包含双引号的合法 JSON 字符串，再将 `<`、`>`、`&` 转为 JSON Unicode 转义；只标记此序列化结果为安全。不能用 escapejs 代替 JSON 序列化。

- [ ] **1. 写失败测试。** 使用已有媒体 fixture 或 bulk_create 初始化媒体，随后必须走真实 Media.save/serializer/form 保存路径；仅 mock 转码、通知等外部副作用，不 mock 被测保存逻辑。
  - `test_description_model_round_trip`：围栏内 `<div>`、`&`、反斜杠、首尾空白保存再 refresh 后逐字符一致；title 的 HTML 仍被剥离。
  - `test_description_api_round_trip`：认证拥有者 PUT 后 GET，description 与原文完全一致，包括四空格缩进、两个尾随空格、结尾换行和空描述。
  - `test_description_form_round_trip`：MediaMetadataForm 验证/保存及编辑 textarea 内容保留相同源码；`</textarea><script>` 不能退出 textarea。
  - `test_description_jsonld_round_trip`：参数化媒体页已有 video/audio/image/其他 JSON-LD 分支，提供含换行、引号、反斜杠、`</script><script>` 的 description；提取每个 JSON-LD 并 json.loads，description 等于源码，无注入 script。同时断言 meta 属性正确 HTML 转义。
- [ ] **2. 运行下面的后端定向命令。** 预期原文/空白保留及 JSON-LD 用例在现有代码失败。
  `docker compose -f docker-compose-dev.yaml exec --env TESTING=True -T web pytest tests/api/test_media_description.py tests/forms/test_media_description.py tests/pages/test_media_description.py -q`
- [ ] **3. 实现保存及编码调整。** 从 strip_text_items 移除 description，保留 title 处理；MediaSerializer 显式定义 description 为允许空串、非必填、trim_whitespace=False 的 CharField；MediaMetadataForm 的 description 字段 strip=False，保留 textarea 原有标签及样式。增加 json_string 过滤器，仅替换媒体 JSON-LD 中 description 的字符串插值位置，删除外层手写双引号；维持既有 summary 条件的兼容行为，不新增 summary。HTML meta 和 textarea 继续默认自动转义。
- [ ] **4. 重跑后端定向测试。** 要求全通过；检查所有 description 消费位置，确认不存在新增原文直插 HTML 的路径，相关发现记入实施记录。
- [ ] **5. 仅提交本任务文件。** 建议提交信息：`fix: preserve media description source and escape output`。

## Task 3: 播放页接入、排版和端到端验证

**Files:**
- Modify: frontend/src/static/js/components/media-page/ViewerInfoContent.js
- Modify: frontend/src/static/js/components/media-page/MediaPage.scss
- Create: frontend/tests/media-page/ViewerInfoContent.test.jsx
- Create: frontend/tests/media-page/descriptionTimestamp.test.jsx

**Interfaces:**
- Consumes: Task 1 的 MediaDescription(source)；Task 2 返回的原始 description。
- 保留 VideoJSEmbed 的 document 点击处理：实际调用播放器 currentTime(90)/currentTime(3750)，不添加第二套跳转事件。

- [ ] **1. 写失败测试。** ViewerInfoContent 接收 Markdown 时产生格式化内容，空白描述隐藏；默认无 summary 时描述直接显示。保留一个模拟现有 summary 的兼容测试，SHOW MORE/LESS 只控制描述可见性。验证四空格代码不会因 ViewerInfoContent 的 trim 变成普通文本。
- [ ] **2. 写真实事件集成测试。** 渲染实际 VideoJSEmbed，mock 播放器及无关加载依赖，将 Task 1 组件置于 document 中；播放器 duration 返回 7200。分别点击 01:30、01:02:30，断言真实现存事件处理调用 currentTime(90)、currentTime(3750)；普通 Markdown 链接和代码不触发 seek。不能在测试中重写一个假的时间戳处理器来代替实际组件。
- [ ] **3. 运行 `npm test -- --runInBand tests/media-page`（frontend）。** 确认格式及原文缩进测试在接入前失败，记录现有播放器行为的基线。
- [ ] **4. 接入组件与样式。** 删除 ViewerInfoContent 中局部 setTimestampAnchors 和 dangerouslySetInnerHTML；仅用 trim 判断空白，传原始 props.description。样式全部限定在 .media-content-description：normal 空白、段落/标题间距、列表缩进及标记、引用边线、可辨识代码背景；pre 使用 white-space:pre、max-width:100%、overflow-x:auto，长普通文本 overflow-wrap:anywhere。沿用主题变量，不修改摘要或评论样式。
- [ ] **5. 执行定向及最终检查。** frontend 中依次运行 `npm test -- --runInBand tests/media-page`、`npm test -- --runInBand`、`npm run dist`；仓库根运行 Task 2 定向测试及 `git diff --check`。新失败与既有基线失败分开记录；不能将环境未启动或依赖缺失写成测试通过。
- [ ] **6. 浏览器验收。** 在隔离的开发/测试数据中创建含完整 Markdown、两个时间戳、长代码、危险输入的描述；用时长超过 3750 秒的可播放媒体验证两个跳转目标。分别在 1440px 和 1024px 桌面视口、浅/深主题检查展示，确认页面无横向溢出且 pre 自身能横向滚动。编辑后刷新验证源码和格式仍在，记录截图及实际播放器秒数；测试数据不写入生产。
- [ ] **7. 仅提交本任务文件。** 建议提交信息：`feat: render markdown descriptions on media pages`。

## 执行准备与交付

- 执行前按 using-git-worktrees 技能检查环境并创建隔离分支/worktree；把本计划作为执行参考，不能覆盖当前工作区的未提交文件。
- 前端命令在 frontend 执行；后端命令来自现有 CI。若测试容器未启动，先检查 compose 端口/卷并启动隔离测试栈，不复用会写入实际媒体数据的数据库。
- 先记录基线，再执行 Task 1 → Task 2 → Task 3；每任务完成一次相关验证，不反复运行不相关重型转码测试。
- 前端安全渲染与后端原文保存必须作为同一版本发布，不能单独上线后端原文保存改动。
- 最终交付包含改动摘要、定向和整体测试结果、构建结果、浏览器验收证据及未解决限制；提交不等于自动推送、部署或发送 GitHub 评论。
- 本文是技术计划，目前未安装依赖、修改产品代码或执行验收测试。用户已选择 inline execution：当前会话顺序实施及自审，不使用任何 sub-agent。
