# Learning Radar（个人学习助手）

面向希望找到可动手实验的单用户学习者，从公开材料发现AI/Agent与生成式软件工程项目，结合本人确认的学习档案做有来源的对照，保存报告、用量和相关项目反馈。默认池偏AI/Agent，不代表整个GitHub；反馈只复用到相关项目与对照，不改长期档案。

当前源码已接入Python原生网页、同源TypeScript/Vite/Three.js增强、持久队列、认证、预算和发布门槛。现有云端旧版周报已经运行，本次新集成版本尚未部署。这里准备本地公开候选，不推送、部署或发布视频。

## 从零运行离线演示

需要Python 3.11+、Node 22.18+或24+、npm和Git。后端使用标准库，前端使用Vite、Three.js及锁定依赖，整个项目不是零依赖。Python和Node须在PATH，也可设置LEARNRADAR_PYTHON为Python可执行文件。

在源码目录执行：

```powershell
Push-Location frontend
npm ci
npm run build
npm test
Pop-Location
python scripts/public_smoke.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 -OpenBrowser
# 只停止本启动器网页和worker，保留数据与额度
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 -Action stop
```

首次npm ci需要包仓库；完整缓存可用npm ci --offline。构建同步原生内联主题和资源白名单；缺商业字体仍可构建和阅读，无须加载屏。离线演示使用独立data/demo-2-1、合成fixture账号和假外部适配器；登录方式见本机演示启动说明及fixture账号常量，不能用于正式认证。打开启动器打印的回环地址，登录→发现→任务→报告→反馈。关浏览器不停止worker，报告带fixture/未实测标记，假邮件不是实际收件。

public_smoke.py自动检查原生HTTP闭环并停止子进程，不读取私人配置或调用真实模型/GitHub/SMTP。必要离线检查：

```powershell
python -m unittest tests.test_evidence_text tests.test_validate tests.test_offline_recheck tests.test_ui_backend tests.test_web
python scripts/public_smoke.py
```

## 本人实际使用配置

只在本人机器配置忽略文件，先确认外发档案的范围和费用：

```powershell
Copy-Item .env.example .env
Copy-Item config\profile.example.toml config\profile.toml
# 本机编辑.env、settings.toml、profile.toml；确认后设confirmed_by_user=true
python -m learnradar.auth_cli init --data-dir .\data
# 密码仅在交互终端隐藏输入，不接受密码命令参数
python -m learnradar.task_cli web --data-dir .\data --web-mode loopback --port 0
# 独立终端；先核对队列和待通知，外发须使用者明确授权
python -m learnradar.task_cli worker --data-dir .\data
```

模型角色由config/settings.toml选择，密钥变量见.env.example；业务模型与编码助手提供方独立。模板不含有效凭据，不指向开发者机器。通知自行复制config/email.example.toml到忽略的email.toml并设置密码环境变量。HTTPS代理另配config/web.example.toml，须验Secure Cookie、Origin和单执行者交接；不能公网暴露回环开发模式。正式停止用各终端Ctrl+C，不按历史PID停止其它进程。

## 数据流与边界

原生表单/CSRF→持久队列→独立worker→受限公开读取/模型适配→结构及引文门槛→发布报告或拒绝草稿→通知→相关项目反馈。增强层只展示和同源GET读取，原生POST、稳定run/repo/task身份及303由Python决定；无JS仍可认证、阅读和反馈。

默认新任务broad为3方向、每方向最多10、合并最多30；显式focused、旧任务及未明确切换的weekly保留原范围。默认每日3个手动任务、每任务4次底层模型请求，实际以配置为准。失败/重试及不确定消费持久记账；价格缺失写费用未知。worker --once可能先处理通知重试，不是指定task开关。

task completed、report published/rejected、email sent/failed/unknown独立。SMTP接受不等于本人收件，unknown不能盲目重发。引文匹配不证明语义正确或项目已安装；区分事实、推断、未实测，不执行候选代码。单用户定位，不扩展聊天、注册/找回、OAuth或多Agent产品团队。

| 路径 | 作用 |
| --- | --- |
| learnradar/net.py、sources_github.py | 公网读取、内网/元数据拦截、取证 |
| task_cli.py、tasks.py、worker.py | 统一入口、持久队列/预算、独立执行 |
| models.py、analysis.py、evidence.py、validate.py | 模型适配、分析、来源和质量门槛 |
| web.py、auth*.py、archive*.py | 原生页面、认证、项目/报告身份 |
| frontend/ | Vite/Three.js、同源资源和原生回退 |
| config/、prompts/、tests/ | 空凭据/合成档案模板、提示词、离线回归 |
| scripts/public_export.py、release_audit.py | 新历史公开候选及脱敏扫描 |
| data/、logs/ | 私人产物，禁止公开Git |

## 来源、隐私与发布

公开候选排除密钥、个人档案/反馈/报告、账号初始化、SQLite/WAL、日志/截图、原私有Git历史和商业NOVECENTO字节；旧个人化示例整份替换为人工合成模板。账号精确输入用技术字体，当前品牌体验保持。

界面复用LBEILC/[RhineLabUI](https://github.com/LBEILC/RhineLabUI)，保留版权和MIT正文。作者可授权GLB/原创配乐与原作第三方权利区分；MiSans、商业字体和依赖独立许可，不称全仓MIT。原创代码公开许可证待所有者确认。[公开说明](docs/PUBLIC-SOURCE-GUIDE.md)包括依据、可选合法字体和扫描边界；另见[反馈规则](docs/FEEDBACK.md)与[视频脚本](docs/VIDEO-OUTLINE.md)。视频只用保存结果，隐藏私人信息，标注复用与AI辅助，不重复生成。
