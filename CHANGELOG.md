# Changelog

## v0.7.0 - 2026-08-18

### Added

- 新增跨系统 `base64://` Record 传输，默认不再要求 AstrBot 与 NapCat 共享本地音频路径。
- 新增后台交付分段长度与单段音频大小上限配置。

### Changed

- 后台长文本改为并发生成独立 WAV，全部完成后按原文顺序逐条发送；并发继续受现有 `max_concurrency` 限制，其默认值调整为 `2`。
- 公共 `synthesize_text()`、Pages 试听和其他插件调用继续返回一个合并后的完整 WAV。

### Fixed

- 修复 AstrBot 与 NapCat 分别运行在 Linux、Windows 或 Android 环境时，本地路径无法被对端读取的问题。

## v0.6.1 - 2026-07-28

### Changed

- `text_and_audio + background` 改为通过 AstrBot `after_message_sent` 钩子提交 TTS；文字走完结果装饰、内建分段回复和正常发送后，后台音频才会开始生成。
- 显式 `/tts` 的文字回复也改回正常结果 pipeline，不再从命令处理器直接发送。
- 后台文字先发改用 AstrBot 官方 `after_message_sent` 能力，并在运行时检查该钩子是否可用。

### Fixed

- 修复插件直接调用 `event.send()` 并清空结果，导致分段回复及其他结果装饰插件失效的问题。
- 修复极快 TTS 任务可能先于正常文字回复完成并发送的问题。

## v0.6.0 - 2026-07-27

### Added

- 后台任务改为原子持久化状态机，支持 `queued`、`running`、`delivering`、`completed`、`failed`、`cancelled`，插件重启后可恢复未完成任务。
- 已生成 WAV 但主动发送失败时，重启恢复只重试发送，不重复调用 MiMo 合成。
- 新增滑动窗口限流、指数退避和熔断保护；只对限流与瞬态服务错误重试，鉴权和无效响应立即失败。
- 新增 `/tts任务`、`/tts取消 <任务 ID>`、`/tts清空 [全部]` 管理命令，以及 Pages 任务查看、取消、历史清理和运行态诊断。
- 新增 AstrBot 主动消息能力预检，Record 发送失败后可实际降级为 File。
- 新增 `v0.5.x -> v0.6.0` 配置迁移和环境变量门控的真实 MiMo API 集成测试入口。

### Changed

- SDK 内建重试固定为零，所有重试统一由插件可靠性控制器管理，避免叠加重试。
- 后台音频默认在发送成功后删除，也可配置为沿用天数/数量保留策略。
- Pages 新增 RPM、退避、熔断、任务持久化、恢复期限、历史容量、平台预检和真实联调开关。
- `/tts状态` 现在显示取消/恢复任务数、熔断状态、请求/重试、限流等待、任务存储与平台能力。

### Fixed

- 修复插件退出或平台短暂离线时后台任务上下文丢失、重启后无法继续交付的问题。
- 修复 Record 主动发送失败时只记录告警但未真正尝试 File 的问题。
- 修复运行时修改任务持久化相关配置后，空闲任务管理器没有重建的问题。

## v0.5.0 - 2026-07-27

### Added

- 新增受控后台 TTS 队列：显式 `/tts` 优先于自动语音，同一会话保持提交顺序，并提供容量、并发、失败通知策略。
- 新增 `background` / `blocking` 交付模式；默认 `text_and_audio + background`，文字立即发送，完整音频生成后主动补发到原会话。
- Pages 和 `/tts状态` 新增排队、运行、完成、失败、丢弃和平均延迟统计。
- 新增长文本 WAV 合并、原子输出、严格响应校验、客户端复用、超时及分类错误处理。

### Changed

- `synthesize_text()` 统一返回一个完整 WAV 路径，多段合成不再向调用者暴露多个文件。
- 模型和格式固定为 `mimo-v2.5-tts-voiceclone` + WAV；默认单请求与分段阈值调整为 2500 字。
- API Key 不再通过 Pages 配置响应返回；输入框留空保存会保留现有密钥。
- 声音样本上传必须显式确认授权，voice store 改为原子保存并保留上一版备份。

### Fixed

- 修复超长句和超过最大段数时形成超大尾段的问题。
- 修复多段合成仅返回第一段、部分失败残留临时文件的问题。
- 修复 AI 导演缓存键未覆盖完整文本、临时上下文、音色描述、provider、prompt 和模式导致的串文风险。
- 修复删除音色时可能删除 `voice_refs` 目录之外文件的路径边界问题。

## v0.4.0 - 2026-06-16

### Added

- 新增普通 LLM 回复自动语音化的群聊/私聊黑白名单访问控制，并支持管理员 ID 绕过名单限制。
- Pages 新增自动语音访问控制规则预览，展示管理员、群聊、私聊三类规则当前生效状态。
- 新增自动语音访问控制日志，记录 allow / skip / denied 的具体原因，便于在真实 AstrBot 环境确认规则是否命中。
- README 新增黑白名单、管理员、UMO/纯 ID 填写说明和真实 AstrBot 环境测试清单。

### Changed

- `/tts状态` 增加自动语音访问控制摘要，便于命令行侧快速确认当前规则。
- 自动语音访问控制规则说明统一为“管理员优先、黑名单优先、白名单非空才收紧”。
- Pages 保存配置成功后会重新拉取最新状态，确保 readiness、访问控制预览和 provider 信息不滞后。

### Fixed

- 修复 Pages 访问控制预览在未配置私聊名单时可能触发未定义变量异常的问题。
- 修复 AstrBot WebUI iframe sandbox 环境下原生 `confirm()` 被拦截，导致删除音色按钮无响应的问题。
- 修复 AstrBot Pages iframe 环境下文档外链弹窗、试听自动播放失败提示和音色操作连点竞态的体验问题。
- 增强 AI 导演失败日志，空异常信息也会显示异常类型、provider、音色、情绪、fallback 状态和超时说明。

## v0.3.0 - 2026-06-15

### Added

- 新增发送前 AI 语音导演，可调用 AstrBot 已配置的 AI 服务商生成隐藏 MiMo 风格指令。
- 新增 AI 优化朗读文本能力，可剔除无意义口头填充并整理自然停顿，且不改写最终聊天文本。
- 新增 AI 服务商下拉选择、手填 provider id、导演模式、失败回退和调试日志开关。
- 新增 AstrBot 日志输出 AI 导演结果摘要，便于确认 `style_context` 与 `speech_text` 是否生效。
- 新增工作台 readiness 状态，提示 API Key、音色库、AI 导演、试听链路是否就绪。
- 新增面向其他插件复用的 `mimo_tts_speak` LLM 工具兼容链路和通用 TTS helper。

### Changed

- Pages 前端重构为 Firefly-inspired 清新玻璃卡片风格，保留原 AstrBot Pages 原生实现，不引入额外前端框架。
- 试听交互增强：不可用时显示明确原因，试听音色下拉仅展示启用中的音色。
- 后端合成上下文结构化，新增 `core/synthesis_context.py` 管理 AI 导演缓存键、上下文合并和日志裁剪。
- Pages AI provider 列表兼容更多 AstrBot provider manager 结构。

### Fixed

- 修复 `mimo_tts_speak` 工具参数与 AstrBot reserved `context` 参数冲突的问题。
- 修复 API Key 通过 Pages 填写后无法稳定持久化的问题。
- 修复上传/试听等 Pages 操作出现成功但前端误报 500 的部分场景。

## v0.2.0 - 2026-06-13

### Added

- 接入 MiMo v2.5 voiceclone 官方 TTS API。
- 新增 Pages 音色上传、音色库管理、试听诊断、多音色切换和默认音色设置。
- 新增情绪路由、长文本分段、输出文件清理、回复模式和自动语音化概率。
- 新增 README、metadata、插件图标、免责声明和基础测试覆盖。
