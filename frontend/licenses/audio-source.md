# 配乐来源与复用范围

配乐复用参考源码包的 `public/audio/atmosphere.ogg`，本地运行地址为 `/static/ui/audio/atmosphere.ogg`。2026-10-11最新用户授权：新访客默认启用（配乐18%，音效28%），主动关闭选择持久保存；旧明确关闭偏好保守迁移。浏览器阻止时首次有效点击/键盘操作启动，不自动改成关闭偏好。

参考作者 LBEILC 的 `public/audio/README.md` 声明：三轨配乐为原创程序编配，无原片采样、外部录音或人声；其原创配乐按参考仓库 LICENSE（MIT）授权。此处仅复用其中 atmosphere 声部，不包含其 MP3、其余声部、音效合成模块或源谱。

已检查参考 `src/audio.ts` 的 click/tick/column/open/back：使用振荡器、短接触噪声及不等比玻璃模态。本项目的 `click.wav / switch.wav / open.wav / back.wav` 由 `frontend/scripts/make-ui-sounds.py` 原创生成：24kHz、单声道16bit、55/85/180/110ms；没有外部录音或原片样本。可从脚本复现。资源通过同源manifest发布，WebAudio真实解码/播放。

`sound.ts` 集中管理一个AudioContext、一份循环配乐和最多一个音效声部；全局80ms节流，高频动作替换旧声部。配乐整段解码后用精确offset循环，避免静态路由无Range时OGG元数据估算影响seek；同场景阅读不换播放器，普通重载只保存会话内音乐进度，不保存业务/鉴权数据。开场、登录及主界面均有声音开关；音量在设置统一管理。实际听感仍需用户试听，解码/波形/播放状态不等于已实听。

没有复制或使用参考项目从《明日方舟》原 PV 提取的三个 38ms 逐字输入短音、typing-preview.wav 或原片对照音频；这些原音不在原创配乐 MIT 范围内。

参考代码和版权许可证保留于 RhineLabUI-MIT.txt；模型及原作造型权利另见 UI-FRONTEND-HANDOFF.md，不能由配乐许可推断。
