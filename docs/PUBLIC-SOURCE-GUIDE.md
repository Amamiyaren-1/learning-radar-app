# 公开源码候选：来源、运行和许可

从明确版本导出允许文件并建立新本地Git历史。原私有历史、真实密钥、个人档案、反馈、账号初始化、数据库、报告、日志和诊断截图全部排除。EXPORT-MANIFEST.json记录来源SHA和实际文件大小/哈希；公开示例档案整份人工合成，资源/能力未知，不含原项目个人化示例。

范围：Python源码、提示词模板、空密钥配置/合成档案模板、离线夹具/测试、当前前端源码/公共资源、许可证、离线启动/检查脚本和README/本文/视频脚本。私有项目设计Skill与历史交接不导出，避免私人路径/上下文传播；不称第三方Skill版权归本项目。

项目中心与首页共享已保存报告中的中文标题/摘要：优先可读已发布中文，其次拒绝草稿并明确标注未发布/未核验；缺字段或损坏数据回退原资料，不请求模型补译。repo原大小写独立展示，仍按规范化repo/run/task定位，原项目简介和来源可展开查看。中文标题、摘要、原简介及repo都支持原关键词查找，标签仍全部匹配。报告舞台独立于72ch正文，桌面阅读模型/正文38/62、手机单列；正文和写表单继续是原服务端HTML，不由展示JSON生成。公开导出不包含任何真实报告或私人展示文本。

## 运行与可选字体

按README执行npm ci/build/test及scripts/demo.ps1。python scripts/public_smoke.py检查隔离fixture原生登录、一次broad发现、任务、发布报告、反馈和停止子进程；模型/GitHub/SMTP真实调用为0，不共享正式数据/账号。正式配置需自行填写忽略文件，使用者显式选择外部配置，不指向开发者机器；公网HTTPS等另行验收。

没有商业NOVECENTO字体、含它的旧dist或历史blob。LR Novecento缺包时swap/系统回退，表单和报告可用。自行获得对应许可后在本机放入Normal400、DemiBold650（本次真实值，其它包须核实）、Bold700，文件名分别为frontend/public/fonts/NovecentoWide-Normal.woff2、NovecentoWide-DemiBold.woff2、NovecentoWide-Bold.woff2。公开候选.gitignore排除这些文件，不追加到公共Git/Release。不能转换Desktop OTF规避许可，自托管Webfont不自动允许字体再分发；通用MyFonts网页不替代对应EULA。

## 实际资源许可

| 内容 | 依据和限制 |
| --- | --- |
| RhineLabUI源码、archive-cassette.glb | LBEILC MIT及当前README范围，保留版权/许可；GLB无嵌入图像，Git blob与上游一致 |
| 原作相关模型造型、构图、∞轮廓 | 存在复用/改编；作者自身许可不替代《明日方舟》等名称、标志、视觉权利，不称全部第三方授权已齐全 |
| atmosphere.ogg | 上游原创程序编配，MIT，无原片样本；保留audio-source.md |
| click/switch/open/back.wav | 本项目make-ui-sounds.py生成，没有复制上游逐字短音 |
| MiSans Regular/Demibold | 原字节未修改，随应用保留Xiaomi版权与MiSans-license.pdf；不独立售卖/分发字体产品，不标MIT |
| Three.js、Vite、Rolling Number、TypeScript等 | 各自许可在frontend/licenses，package-lock固定版本 |
| 项目原创代码 | 最终公开许可证待所有者确认，不伪造作者/手写比例，不把独立资源并入全仓MIT |

不包含archive-assembly、Blender、原PV、逐字短音或其它上游声部。核对上游提交12cc5e4013acb9408f753ff71de6b8492299b7c8（2026-10-13），其README明确点名archive-cassette.glb并允许作者可授权资源再分发，仍排除原作/独立资源。GLB SHA256为dda42b9b3b471d11c69820a64084d254a64b27761287ab0e35dd8387ec0a0bed，Git blob为a52bce6ddc49c8ead438f0a07def814e50e6f885；只更新许可记录，没有升级前端或模型。

## 公开前复核

扫描新源码、Git历史和实际构建；.gitignore不能删除旧提交。启发式扫描需要人工复核，发现真实凭据曾提交须先轮换。测试合成token与真实秘密分开，不能把误报当泄露或称扫描穷尽所有秘密。本地候选无Git remote，本地通过不等于授权推送/部署/发布视频。
