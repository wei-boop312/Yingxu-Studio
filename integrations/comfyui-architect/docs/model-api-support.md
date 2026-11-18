# v2.8.0 模型与API支持：能力声明不等于已连接

新增“模型/API支持”。本地知识检索与人工ChatGPT交换保持可用，未部署小语言模型。联网默认未配置/关闭，不读取用户环境变量、浏览器会话、保险库或磁盘旧秘密。页面不接受API key，不保存/启用草稿配置。模型ID必须从服务商复制，不根据GPT名称猜测。

非密钥配置完整字段：endpoint、model、protocol、token_parameter、max_output_tokens、timeout_seconds。支持chat_completions（max_tokens或max_completion_tokens）及responses（max_output_tokens）；不自动带temperature/工具/JSON模式以免不兼容；响应必须完整严格JSON方案。Responses store=false，无后台/流式调用；第三方是否实际遵守须另验。协议/参数声明不等于任一站点或模型认证。

规划上下文补充具体受控模板graph_contract和真实节点输入输出类型/必填合同，不只给节点名称；不包含LoadImage选择列表、图片字节、绝对用户路径、密钥。运行profile与模板maturity分开，stable不是质量承诺。上下文改变后旧digest需重新导出，不能继续使用旧方案。

联网服务PlannerProvider只有可信Python宿主能安装到ArchitectService.api_provider；HTTP没有配置密钥或启用端口。宿主须明确提供完整固定profile、credential_reader(scope)、approval(scope)。批准严格True且在凭证之前，scope绑定实际目标、模型、上下文；前端确认profile_digest防目标变更。不要把approval简单恒True当作真实预算控制：它须接另获授权的费用政策。实际未接入任何用户秘密或真实价格/账单。

联网入口只发文本规划，POST api/propose须本机同源JSON/16KiB与单次外发/可能收费明确确认。原API输入清单、图片和生成/安装接口不外发。结果仍经原完整当前方案校验，虚构模板节点来源拒绝，不自动生成/排队。UI可将有效方案交给第三阶段。没有真实配置时按钮禁用并明确未配置，不冒充智能规划成功。

原生HTTPS实现：仅TLS443，无代理/重定向/重试；全部DNS地址须公网且非IPv4映射，选固定实际地址连接，Host/SNI/证书用原域。默认系统CA；生产不提供自有CA或回环开关。请求/响应128KiB，头100/16KiB，JSON不压缩/不重定向，单次5到30秒/单调检查；固定公开错误不泄露端口秘密异常。一provider一个真实工作槽，DNS/非合作端口未结束不释放；async路由31秒超时设置取消，迟到结果不采纳，不重发。系统DNS/同步端口不能强制中断，不是全局沙箱/跨进程硬闸门。凭证仅当次读取、无本实现持久保存，不能保证Python/SSL内部可靠擦除。

**费用边界**：token上限不是货币硬上限；超时/拒绝/坏返回不证明未计费。没有真实预算账本/供应商账户硬限额验证，不声称能强制站点按单次金额结算，须明确费用批准与站点限额后另做一次真实验收。默认不自动重试/并发。Hubway仅收到域名，公开页面为JS壳，未确认API路径/模型ID/定价；不把dashboard当端点，不探测付费接口。

测试230单元（新增22支持+3自有TLS），55 mockDOM场景（新增6）。TLS在独立临时目录生成自有CA/随机私钥，仅测试注入DNS/socket/CA，实际回环TLS收发/Host/正文、证书拒绝无POST、302与非JSON拒绝；没有供应商DNS/网络/费用。首TLS运行暴露Connection:close后读已关闭socket错误，实质修复到自有响应生命周期再通过。一次定向unittest命令缺portable sys.path在导入阶段失败，不当作套件运行；改正确路径不调超时依赖。全回归保留原断言。

官方参考：
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
https://developers.openai.com/api/reference/resources/responses/methods/create.md
后者web工具因长度/content-type抓取失败，实际HTTPS只读获取200/506638字符核对max_output_tokens/output_text/completed；不冒充Hubway兼容性证据。

仍缺准确模型ID、完整API地址、币种/单次费用上限、具体凭证配置授权/来源与独立费用批准；未接真实API前智能规划效果未验收。此前换装质量问题也未因增加API协议支持自动解决。
