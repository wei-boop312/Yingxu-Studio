# 第二阶段：规划交换离线部分（v2.6.0）

新增“大模型规划交换”：生成上下文→导出提示词→用户交给ChatGPT→粘贴纯JSON回复→本机重核→导出已校验方案。没有读取网页会话/自动调用模型。提示词含需求、具体兼容模板指纹、来源和节点类，不导出LoadImage选择列表/图片字节/本机路径/凭证。需求文本由用户控制，导出前不要放秘密。

POST planner/context只接受text/profile。POST planner/validate只接受text/profile/proposal；上下文digest绑定原需求、语料版本、实际扫描generation、具体模板及节点签名。重新扫描可能使旧方案过期，须重新导出，不采用旧环境方案。导入仅允许当前兼容模板/其来源/实际节点类，步骤须完整覆盖节点类，未知工作流或执行命令/虚构节点/省略步骤被拒绝。模型回复是数据，不执行reason/title中的任何文本。节点类覆盖不等于参数或图片正确，第三阶段仍须绑定并编译。

兼容适配器目前仅纯请求序列化与有界响应解析（无网络）：明确HTTPS chat/completions端点、模型ID、512至4096输出token；不接受密钥、不自动发起请求、不支持工具调用/流式/截断/Markdown/重复键/NaN。使用messages/content/choices/finish_reason结构，参考OpenAI官方接口：
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

这不是对Hubway/任何模型的兼容认证。max_tokens/temperature也不是所有新模型均支持，真实目标确定后再按供应商合同适配。当前URL检查仅为序列化参数规则，不是网络SSRF防护。

**尚未完成的真实API部分**：精确供应商模型/端点协议、费用币种与单次上限、明确安全凭证配置授权、网络发送/取消/超时/限额与真实请求验收。没有HTTP调用端、凭证存储器或实际预算强制执行，不声称第二阶段全部完成。不得将ChatGPT订阅/网页登录当API凭证。用户可以先走人工交换，真实往返仍须提供模型回复验收；模拟回复不等于模型思考质量。

离线验收191项Python单元（新增20）、39项mock DOM场景（新增8）；实际8188清单→导出上下文→明确synthetic响应→解析→本地验证并生成phase2-offline-acceptance.json。未付费/外发图片/生成工作流或图片/读取秘密。新路由和真实界面仍待重启核验。此阶段产物是受具体模板约束的结构化方案，不是最终工作流/图片；第三四阶段必须另验。
