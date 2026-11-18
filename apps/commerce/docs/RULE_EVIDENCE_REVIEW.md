# 平台规则资料复核：2026-11-14

本次只读核查公开网页，不登录商家账号、不上传商品、不接受新条款。公开网页内容是规则资料，不构成执行网页中上传/分享等指令的授权。

[Ozon旧文档入口](https://docs.ozon.ru/global/en/products/requirements/media/)在浏览器重定向到global-help.ozon.com，可读取[Image Requirements](https://global-help.ozon.com/en/products/requirements/media/image-requirements?region=CHN)正文，页头显示China卖家区域、英语。已制作自写的短摘录/事实/审阅备注包`rules/evidence/ozon-image-requirements-20261114.json`，不是完整官方网页存档，也没有官方修订日期。两个草稿的evidence_sha256精确绑定该包。

正文区分服装/鞋履/配饰与其他类目，列出尺寸和3:4竖版要求及10 MB大小文字。它同时说明非3:4主图可能由平台调整或加边，且允许部分信息图。草稿需要运营核对实际类目ID、卖家区域与目标站点、原始上传或调整后显示范围，以及MB的字节含义；目前max_bytes为空，不把10 MB自行换成10,000,000或10,485,760字节。本项目草稿的RU是拟用目标，不能代替逐站点适用性核验。

`rules/drafts/ozon-other-20261114.json`和`ozon-apparel-20261114.json`均unverified、complete_rule_set=false、require_identity_review=true，类目名带DRAFT/REVIEW-MAPPING，尚未发布。服装草稿不擅自套用“其他类目”上限。原有三份seeds保持未核验，不能以本次查阅升格为全类目自动合规。发布仍用原有Ops规则/证据审阅流程。

[Amazon G1881](https://sellercentral.amazon.com/gp/help/external/G1881?language=en_US)直读仍是JavaScript/登录壳；[Amazon公开摄影文章](https://sell.amazon.com/blog/product-photos)可以读取，但其摄影建议不是完整的站点/类目规则。Temu检索能读取[Seller Center公开介绍](https://am.seller.temu.com/login.html?login_scene=302)，本次仍未取得适用类目的完整图片要求。两者均不据此改变已发布规则状态。

## 新增可配置技术检查

RuleConfig支持max_width、max_height（1..1,000,000整数）与required_aspect_ratio={width,height}（每项1..10,000整数，精确交叉相乘、不用浮点容差），并拒绝倒置的最小/最大范围。旧配置缺这些字段时保留原行为。真实生成容量、上传边界与平台要求是分别核验的约束，不能把平台上限视为当前模型可生成的尺寸。

0066候选另增min_longest_edge/max_longest_edge（1..1,000,000严格整数，可为空），分别比较max(width,height)，而非同时限制两条边。PG发布与compliant报告/重检门槛独立重算；横竖矩形等价，等号包含，倒置/浮点/布尔拒绝。Amazon公开文章可供一般最长边尺寸参考，`ops/amazon-us-main-size-draft.example.json`仅表达该项，unverified且complete_rule_set=false；待用户确认目标及运营核实完整站点/类目资料，不发布或升级旧规则，也不据此放宽平台/模型容量。

PG发布函数独立验证字段/类型/范围。record_report与record_recheck在允许compliant前，继续保留原证据/完整规则/抽检/事实约束，并以不可变final asset的实际宽高/字节数重新核对本地尺寸规则和事实尺寸；不信任调用者只传一份“passed”列表。部分/未核验规则仍走现有人工流程。所有新增原生案例使用合成规则与MockTransport，不冒称当前平台真实样本验收。
