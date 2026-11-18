# SOP 闭环支撑审计

状态：15 项均未在本次导入中修复。5 项复现问题、10 项 SOP 支撑缺口。

## D-01 · P1 · 输出端口越界可被标为可执行，极端负索引触发异常

类型：REPRODUCED_DEFECT。

代码：[integrations/comfyui-architect/workflow_architect/validator.py:146](../../integrations/comfyui-architect/workflow_architect/validator.py)。SOP：第18/19章；执行前验证。

观察：只有一个输出时，索引99和-1均返回valid=true、execution_ready=true；-99抛IndexError。编译器虽检查正向上界，独立validate入口仍可误放行。

影响：编辑或导入的无效工作流通过本地校验，直到宿主拒绝或本地异常，无法稳定判断可执行性。

修复方向：独立校验输入/输出端口名称、整数类型、0<=索引<输出数量；所有入口统一返回结构化错误。

验收：正常图通过；负索引、超大索引、bool索引和未知端口被拒绝；任何输入不产生未处理异常。

## D-02 · P1 · 循环依赖工作流被标为可执行

类型：REPRODUCED_DEFECT。

代码：[integrations/comfyui-architect/workflow_architect/validator.py:79](../../integrations/comfyui-architect/workflow_architect/validator.py)。SOP：第18章制作路线可执行性；架构师工作流验证。

观察：两个IMAGE节点互相依赖、并连接SaveImage的合成图返回valid=true、execution_ready=true。当前仅记录边并判断保存节点有前驱，没有检测有向环。

影响：专业工作流的校验结果不可靠；不能将现有validator作为统一平台的可执行性闸门。

修复方向：建立完整DAG检测与输出可达性检查；具体模板验证与独立validate均覆盖。

验收：自环、双节点环、脱离输出的环均拒绝；正常DAG与无害共享子图通过。

## D-03 · P2 · 未知输入和超出节点数值边界的参数未被拒绝

类型：REPRODUCED_DEFECT。

代码：[integrations/comfyui-architect/workflow_architect/validator.py:127](../../integrations/comfyui-architect/workflow_architect/validator.py)。SOP：第18章输入支持所选方法；执行参数校验。

观察：额外输入unexpected_parameter及声明min=1的steps=-5均返回可执行。Registry规范化未保留min/max等完整参数合同；validator主要检查必填、链接类型及枚举。

影响：真实节点或新版本参数差异可被漏检，增加运行失败；输入已列出不能等同完整Schema验证。

修复方向：保留真实节点参数Schema，校验字段存在、标量类型及边界，必要时请求宿主预检。

验收：数值边界、类型、枚举、未知字段、缺失字段都有真实反例测试；与宿主校验结果对照。

## D-04 · P1 · 详情页运行器缺少跨实例的任务所有权和去重

类型：REPRODUCED_CONCURRENCY_RISK。

代码：[integrations/comfyui-architect/web/js/detail_panel.js:62](../../integrations/comfyui-architect/web/js/detail_panel.js)。SOP：第12/21/25章任务恢复、版本与成本记录。

观察：两个独立内存journal共享类似浏览器存储并并发运行同一组六项任务时，模拟submit被调用12次。localStorage保存回读和一次队列检查没有跨窗口原子领取能力。

影响：多窗口或多个调用方可能重复消耗GPU并覆盖进度指针。此为真实runner的并发模拟证据，尚非真实浏览器/ComfyUI并发验收。

修复方向：将领取、幂等键、执行回执与恢复进度移到受控服务，建立跨调用方租约；未知提交保留对账。

验收：两个会话同时启动同一任务只创建一组提交；断线、保存失败与响应丢失不造成未知任务自动重投。

## D-05 · P2 · 离线CLI尺寸负例可因缺依赖而误通过

类型：REPRODUCED_TEST_FALSE_POSITIVE。

代码：[apps/commerce/tests/contract/test_offline_vision_sample.py:54](../../apps/commerce/tests/contract/test_offline_vision_sample.py)。SOP：稳定性证据：失败用例须验证正确失败原因。

观察：2049x2049合成图的CLI在导入httpx时退出1、未建输出目录，恰好满足现有尺寸负例的两个断言。现有套件该项记录PASS，但并没有执行图片尺寸判断。

影响：单看PASS数量会把环境损坏误当成边界保护有效；不能据这一通过用例宣称已验证4MP限制。

修复方向：先验证CLI依赖与启动基线，再断言结构化错误码或专属失败原因；区分启动失败与受测业务拒绝。

验收：缺依赖应明确失败或标环境阻塞；完整环境中只有触发预期尺寸错误才使该负例通过。

## G-01 · P1 · 缺少商品SKU与套图项目、图号的正式业务对象

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/src/visual_platform/api/products.py:17](../../apps/commerce/src/visual_platform/api/products.py)。SOP：第3/7/10章；F01/F06/F08。

观察：产品正式输入为name/category，任务输入是单一product/source/generation/targets；未见套图项目、SKU变体、图号/购买问题、每图证据和待补图号的正式合同。自由文本可以记录这些信息，但不能执行对应规则。

影响：只能串联单张任务，不能稳定管理SOP的一套商品图片及跨SKU隔离。

修复方向：在业务平台补充产品变体、制作项目、逐图任务和作用范围；与既有资产及任务ID建立关系。

验收：同型号不同SKU不互借事实；项目可指定图数、任务类型和市场；每图有独立状态与资料要求。

## G-02 · P1 · 外观理解已实现，但未形成SOP的多面证据与角度约束

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/src/visual_platform/domain/appearance.py:13](../../apps/commerce/src/visual_platform/domain/appearance.py)。SOP：第4/5/17章；F02/F03/F04。

观察：已有读单图、文字/颜色/材质草稿、人工确认和原图绑定。正式AppearanceConfig未表达逐面状态、接口所属面/顺序、独立配件、允许角度及禁用面。uncertainties存在于识别原记录，但确认配置及生成输入未将它作为结构化限制传递。

影响：文字约束可以辅助重绘，不能保证未知面、接口和配件等SOP约束在后续任务中可检查。

修复方向：保留现有理解确认流程，在其上建立带来源和版本的结构卡、角度卡、配件卡及未知/冲突状态。

验收：未知底面不能被策划为允许角度；接口改面及SKU冲突被标明；每个锁定项能回到原始证据。

## G-03 · P1 · 固定六模块不能承载可变套图及部分待资料继续制作

类型：SOP_CAPABILITY_GAP。

代码：[integrations/comfyui-architect/workflow_architect/detail_page.py:52](../../integrations/comfyui-architect/workflow_architect/detail_page.py)。SOP：第2/7/21/25章；F06。

观察：详情页接受恰好六个有序模块，编译固定512x640；十模块和五模块探测均被拒绝。分图与长图导出都要求六图全部done。六张本身并不违背SOP，问题是没有按任务范围调整或保留部分图待证据的能力。

影响：无法表达09尺寸/10包装有条件待补、其他图继续且可独立交付的通用套图。

修复方向：将固定六模块保留为一种模板，上移套图编排到可变逐图计划，独立处理资料阻断与交付状态。

验收：图数可按任务指定；09/10缺证据保留编号；其他图可继续、复核及明确范围交付。

## G-04 · P1 · 事实编号校验不等于卖点证据、SKU与文案的可追溯性

类型：SOP_CAPABILITY_GAP。

代码：[integrations/comfyui-architect/workflow_architect/detail_page.py:18](../../integrations/comfyui-architect/workflow_architect/detail_page.py)。SOP：第6/7/16章；F05/F13/F14。

观察：product只接受name/facts字符串，为事实分配本次F编号，再检查计划引用存在。加入sku/evidence_refs被拒绝。现有提示词库具备来源及版本校验，但未将当前商品事实、条件、准确画面文案和参考职责串成SOP单图执行卡。

影响：能限制模型只选已填文本，不能判定文本是否属于当前SKU或是否有规格、配件等依据。

修复方向：建立事实/来源/适用SKU/条件/文案对象，逐图引用；F13/F14合并显示并复用已确认版本。

验收：同一事实在不同SKU适用性可区分；缺证据的尺寸/包装/性能不能自动成为可发布文案。

## G-05 · P1 · 已有合规与人工记录底座，仍缺SOP逐图及整套验收

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/src/visual_platform/domain/facts.py:27](../../apps/commerce/src/visual_platform/domain/facts.py)。SOP：第11/19/21/27章；F07。

观察：检测事实集中于文字/Logo/水印/白底/主体范围，规则与人工复核有记录。未见基于锁定卡的接口、配件、未知面、场景尺度和整套节奏的结构化检查。业务下载按执行succeeded放行，架构师导出按done放行；没有统一的发布版交付验收状态。

影响：可取得图片与合规报告，但不能据此把生成成功计为SOP合格成品。人工运营工具不等于日常逐图对照工作台。

修复方向：独立执行状态、验收状态和交付角色；草稿可下载，发布版只选择已验收文件，未知与阻断项不能被总分抵消。

验收：生成成功但接口错面的图保持返工；草稿导出有标记；整套完成数只统计实际验收通过的独立文件。

## G-06 · P1 · 两项目尚无统一任务、资产与Comfy执行桥接

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/src/visual_platform/providers/live_bailian.py:85](../../apps/commerce/src/visual_platform/providers/live_bailian.py)。SOP：第7/12/18章；统一闭环执行。

观察：业务Worker路由为offline/bailian_live等现有提供者；架构师直接用Comfy前端API提交prompt并用浏览器journal记录。未见业务任务ID到prompt_id、资产版本、结果回收及统一授权的连接适配器。

影响：当前可以人工搬运资料与结果，不能将两个仓库并置后视为同一持久生产闭环，也没有本轮Comfy Desktop实机集成证据。

修复方向：新增统一执行适配与结果归档关系；复用业务侧幂等、租约、回执及不可变资产原则，保留Comfy专用协议。

验收：同一商品图能从业务任务提交到Comfy，回收带哈希结果，刷新后恢复；未知提交不重投；多端不可重复领取。

## G-07 · P2 · 缺少面向SOP套图的统一交付清单与导出适配

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/src/visual_platform/domain/schemas.py:34](../../apps/commerce/src/visual_platform/domain/schemas.py)。SOP：第8/12章；附录A交付。

观察：业务任务输出合同为PNG，下载名final.png；六模块可导出分图和长图，但没有统一的SKU/市场/语言/图号/风格/版本交付清单。特定品牌首屏另有1600及分层包能力，不能推广为通用整套服务。

影响：SOP要求的按项目导出、独立文件、真实源文件声明、待补图号和多平台版本仍需人工管理。

修复方向：建立可配置导出规格及逐图交付manifest，保留内部默认与平台要求的来源区别；只声明实际生成的源文件。

验收：按项目规格导出正确比例/色彩/文件大小与命名；版本与待补项可追溯；不把长图或开发交付包当作整套商品交付。

## G-08 · P2 · 已有任务快照，缺少事实修订对下游计划的影响追踪

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/src/visual_platform/services/quotes.py:77](../../apps/commerce/src/visual_platform/services/quotes.py)。SOP：第12/21章；F07/F08。

观察：报价冻结外观revision、原图哈希及规则，历史任务快照有价值；外观更新增加revision。未见R参考→结构项→P文案→逐图计划→多语言风格成品的依赖关系及待复查传播。架构师通过上下文摘要使旧计划失效，但不是逐项影响分析。

影响：不能在修改一个接口或规格后稳定列出应复核哪些图，也不能只重做受影响部分。

修复方向：补充可追溯引用关系和新版本影响清单，历史任务快照保持原样。

验收：修订一项事实能列出受影响图号与文案；无关图保留；旧任务仍可重建原始依据。

## G-09 · P2 · 前端市场与图片用途固定，未覆盖SOP发布位置及语言

类型：SOP_CAPABILITY_GAP。

代码：[apps/commerce/apps/web/src/App.tsx:57](../../apps/commerce/apps/web/src/App.tsx)。SOP：第3/8/16章；F01/F05。

观察：当前前端将ozon设为RU、其他平台设为US，并固定image_type=MAIN；没有任务语言字段。后端Target可表达其他市场/用途，但前端未提供相应操作；Platform枚举目前为amazon/temu/ozon。

影响：副图、详情、不同国家语言等需求不能通过现有日常界面准确提交，不能直接推广到SOP全部平台。

修复方向：按项目选择国家、语言、类目和槽位，绑定具有证据状态与日期的规则；扩展平台须相应补规则与验证。

验收：同SKU可分别建立主图/副图及不同语言版本，不机械复用MAIN规则；未核实规则保持人工复核。

## G-10 · P2 · Comfy绑定以文件名和节点登记为主，未固定原始素材与模型字节版本

类型：SOP_CAPABILITY_GAP。

代码：[integrations/comfyui-architect/workflow_architect/plan_compile.py:51](../../integrations/comfyui-architect/workflow_architect/plan_compile.py)。SOP：第4/12/18章；输入版本与真实参考。

观察：已校验真实模型/图片选项并使用上下文及工作流摘要，但图片绑定主要为LoadImage文件名，模型为枚举名称；foundation明确file_integrity_verified=false。缺少与业务资产版本哈希及权重内容一致的运行快照。

影响：同名文件被替换时，计划/工作流摘要不能独自证明执行素材相同；统一平台的历史任务追溯尚不完整。

修复方向：连接执行层时按固定资产版本提供输入；记录模型/节点环境可核验的版本或明确未知；执行回执与结果哈希绑定。

验收：原图同名替换、模型变更和环境差异可被发现或明确标未知；旧任务不静默使用新素材。
