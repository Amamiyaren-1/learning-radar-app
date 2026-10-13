# 新版本部署制品方案（仅本地评审）

scripts/build_release_artifact.py将确定SHA的独立公开候选与其实际前端构建一起打包。读取EXPORT-MANIFEST的明确允许文件、frontend/dist/manifest.json声明的资源及清单本身；源码必须干净且与最终导出hash一致。包内ARTIFACT-MANIFEST记录公开源码SHA、导出源版本、每文件大小/SHA和构建清单。不是只含后端的旧部署包，不拷贝私人工作区或Git历史。

本地审阅包不含个人档案/反馈/报告、数据、配置凭据/账号/会话、诊断与对话、商业Novecento；保留MiSans和第三方许可，字体缺包使用现有swap回退。需要保持私有正式Webfont视觉时，另核实生产域名许可后以私有受控制品携带18资源，不加入公开Git/Release，也不转换Desktop OTF。本地私有正式视觉不因此改为公开回退版。

```powershell
# 在私有源码仓库，指定已构建的独立候选；输出必须是新的本机data文件
python scripts/build_release_artifact.py --candidate data/public-release-20261013 --out data/review-artifacts/learning-radar-review.zip
```

ZIP仅是可核对、可抽取冒烟的评审制品，未推送/发布/部署；最终用于哪一个正式源码SHA、商业字体版本和原创代码许可证须在实际部署评审中固定。构建不包含node_modules；发布者按锁文件构建后交付dist，云端运行不需要重新下载前端依赖。源码和运行配置分离，但必须先核对现有配置加载优先级与真实路径，不盲目覆盖config/settings.toml或重新初始化账号。

现有云端周报继续运行。本轮不SSH、不暂停timer、不修改服务、安全组、周期或数据库。下一次切换需现场确认目标主机/维护授权、实际源码与数据/config/env路径、唯一worker与旧CLI/cron/timer、systemd/Caddy/Python版本、域名/TLS、字体许可和可回滚版本。当前本机回环/Windows不能代替Linux或公网HTTPS。

1. 只读盘点旧service ExecStart/环境文件/锁、timer周期与Persistent、已有未终结任务/通知及数据引用。weekly原方向范围、周期ID/时区/触发时间保持，不因网页默认broad而切换。
2. 在线SQLite备份和同批runs/reports文件清单/hash；单独受限备份配置/档案，不放评审ZIP。备份副本做恢复/迁移演练，保留旧validation/发布状态/预算/反馈/邮件，核对新旧程序兼容性。
3. 核对同版后端、模板、dist、manifest和实际HTTP/MIME/hash；在远端隔离fixture运行离线冒烟、无JS/深链接/拒绝报告与表单，不碰正式数据。部署包SHA与构建源SHA一致，保留旧代码和配套前端资源。
4. 授权维护窗口才停止已确认timer的新触发，等待在途任务和通知完成。确认旧执行者退出后启动一个新worker；旧待通知/unknown需先人工审阅，不重发或清额度。切换版本不覆盖正式数据/配置/账号。
5. 现场验证HTTPS、Secure/HttpOnly/SameSite、认证失效、Origin/CSRF、同源资源/CSP、新旧链接、任务/报告/邮件三轴。真实新任务/SMTP需要该次独立授权，不能继承本轮一次真实任务额度。
6. 确认单执行者后恢复原timer及原周期范围，不补跑旧周期。失败先停新触发/新执行者，回退旧代码+同版前端；先保护新增数据再评审整批快照恢复，不只回退数据库，不重复unknown通知。记录切换/回滚SHA、数据兼容与预算保留证据。

尚未验证：Linux/systemd实际启动、远端路径及恢复演练、公网TLS/Secure Cookie、生产字体域名许可、跨浏览器/实体手机、真实后台暂停和系统减少动态切换。不得将本地包、假SMTP或回环HTTP称为这些验收通过。
