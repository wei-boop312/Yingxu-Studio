# 离线交付包

2026-11-15 本机运行环境已启动：沿用项目专属原生PG/MQ/S3与私有账号，先备份开发库，再从0024迁移到0066；备份中100个原用户ID全部保留，另新增1个隔离浏览器测试账号。网页/API健康200，真实本机浏览器完成登录、产品、上传、Konva选区、离线生成、报告下载及移动端闭环（1项/11.9秒）。默认offline，0付费模型调用。桌面已创建启动和打开入口；本机私有启动器重复执行返回READY，无需Docker。未配置Windows登录自动启动。详见LOCAL_WINDOWS_TRIAL.md与local-trial-start-20261115.json。

2026-11-15 初版交互提交5bc6c273/CI43709997483整轮成功：225单元、133契约、278真实PG/MQ/S3、6普通浏览器（4真实服务闭环+2合成HTTP界面）和11受控浏览器，完整8服务Compose、独立恢复与离线门槛均通过；1790文件交付包本机独立OFFLINE_HANDOFF_VERIFIED。原始报告和日志摘要保存在docs/evidence/ci-43709997483。本轮零付费调用，工作台仍离线；IMG-001质量优化按用户决定延期。初版可交付试用，完整V4及日常真实模型接线尚未完成。交付包生成时本机后端尚未启动；现已完成本机启动与实际浏览器闭环，见本页最新记录。

初版操作、已知限制与反馈收集见[初版试用说明](INITIAL_TRIAL.md)。新增界面交互已由5bc6c273完整CI验证，对应包已本机独立校验；历史包不代替本次交付包。

2026-11-15 用户同意将IMG-001出图回贴色块/接口错位留档，初版提交并试用后再完善；作为已知限制，不阻断初版试用提交，也不视为质量通过。详见[试用后改进记录](POST_TRIAL_IMPROVEMENTS.md)。仅暂缓质量优化，其余项目接线、验收和交付工作继续，未宣布项目完成。

路径校验拒绝Windows设备保留名（含扩展名及COM/LPT上标数字）、末尾空格或句点和非法字符，规则依据[Microsoft文件命名说明](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)。恶意名称在读取ZIP清单时拒绝，不尝试解包或创建设备文件；正常近似名称保留。新增本地边界测试与原3ef9eea实际包复验通过，不代表后续提交已经完成整轮CI。

清单、门槛与许可引用JSON也拒绝重复字段及NaN/Infinity，不能靠摘要重封接受含歧义的门槛内容。该边界有离线恶意归档测试；来源信任仍须核对可信CI的commit和sidecar摘要。

CI完成当前提交的静态、单元/契约、原生PG/MQ/S3、生产浏览器、完整Compose、冷恢复/重启和全部offline gate后，执行`python scripts/handoff.py`，生成`work/ci-public/offline-handoff.zip`和摘要报告`offline-handoff.json`。两者随该提交的GitHub Actions artifact上传；artifact按仓库现有设置保留3天。先核对报告commit，再下载对应包。

包内目录：

- `source/`：`git archive HEAD`的准确受版本控制源码，含锁文件、运行说明和历史证据。
- `frontend/`：当次生产构建，每个文件必须与原生浏览器测试记录的摘要一致。
- `evidence/`：当次5个原始门槛JSON，全部必须同commit、通过、零付费调用，静态报告明确实现目录干净。
- `supply-chain/`：当次生成的Python/npm SBOM、实际打包许可文本、NOTICE及其摘要引用。
- `HANDOFF.json`：每个文件的字节数/SHA256、源码提交和未验外部门槛。

下载后在已安装工程依赖的环境校验：

```sh
python scripts/handoff.py --verify <offline-handoff.zip的路径>
```

返回`OFFLINE_HANDOFF_VERIFIED`只证明包的内部完整性与对应离线证据约束。核对可信的GitHub运行来源、侧边报告SHA和commit，才能确认来源；ZIP清单并非数字签名。包内历史source/docs/evidence不替代本次evidence/，不同提交的绿灯不能互相外推。

验证器拒绝重复/大小写冲突成员、路径穿越、绝对路径、反斜线、私有.env/工作目录、符号链接、超限成员、摘要不符、陈旧/失败门槛、浏览器验收后变更的前端以及缺失/变更的许可文本。创建器再次运行现有P5构建校验，再从准确提交导出源码；输出文件仅新建，不覆盖既有包。默认测试用合成fixture校验拒绝条件，实际包生成必须由干净CI完成。

此包状态为`OFFLINE_READY`，provider_verified、production_deployed、release_authorized均false。P4-03真实供应商、质量与实际账单，以及P5-03目标环境、适用规则/权属与发布决定仍待完成。运行完整服务按包内RUNBOOK和Compose说明初始化专属环境和私有凭据；本包提供源码、前端构建和验证证据。原项目代码的许可状态见NOTICE说明，不因打包而赋予新的开源许可。


首次实际交付包已在[9248667 / CI43520402375](https://github.com/wei-boop312/-AI-/actions/runs/43520402375)生成：1282文件、4,467,445字节，下载到本机后再次独立校验通过。原始sidecar和嵌套ZIP SHA见docs/evidence/ci-43520402375；后续提交仍须生成/验证属于自身commit的新包。

最新完整基线[8f7687a / CI43531050538](https://github.com/wei-boop312/-AI-/actions/runs/43531050538)的实际包含1362文件、4,678,394字节，侧边SHA为7fc0d641bf1250e3d456dfe126fc3d8d1c6b71550461f0917dd41607bc534a65。本机下载后独立验证OFFLINE_HANDOFF_VERIFIED，原始sidecar、日志计数和来源摘要保存ci-43531050538。它不含后续上传/报价界面及SSE循环修正。

最新完整候选[c4d1105 / CI43536961836](https://github.com/wei-boop312/-AI-/actions/runs/43536961836)：181 Python单元/56契约/223原生/4普通+10受控浏览器及全部门槛PASS，实际包1404文件、5,179,941字节，SHA为07b29b7ab776831cd2ed9af712378f2c6eaa8f7c188793496c5d0362356f9b5f；下载后独立验证通过。原始sidecar和上传/报价专门PASS报告及原日志摘要保存在ci-43536961836。0046独立恢复重放和启动参数兼容是后续提交，不借用此包的已验范围。

[3ef9eea / CI43537600900](https://github.com/wei-boop312/-AI-/actions/runs/43537600900)已进一步通过0046独立存储恢复后的原重检回执重放，181单元/56契约/224原生/4+10浏览器与全部门槛全绿。实际包1406文件、5,183,182字节，SHA为8bf76e10224199928dfb3a32bbe4a1df0b3958bf11d4830d3098c327535ae20a，下载后独立验证通过；原始sidecar及来源见ci-43537600900。

[339cebb / CI43539701837](https://github.com/wei-boop312/-AI-/actions/runs/43539701837)进一步包含offline启动参数兼容并整轮通过，测试数量181/56/224及4+10浏览器。实际包1423文件、5,340,362字节，SHA为48ee3fcb87fc0f68ef20fcaf9e73a2f449c5461c38a2e470d94cc306bd468bd9；原始sidecar和来源见ci-43539701837，新Windows路径校验独立复验通过。后续身份请求清理与路径加固仍分别等待自身CI。

[d5e3440 / CI43540917648](https://github.com/wei-boop312/-AI-/actions/runs/43540917648)已验证初始身份请求清理，181/56/224及4+10浏览器和全部门槛通过。实际包1432文件、5,349,687字节，SHA为d2b26c45613c6c1e3f79ade14e05b5dd37b9892386e9869e966c058634059cca；原始sidecar和来源见ci-43540917648，下载后经最新Windows路径规则独立复验通过。该包不包含后续路径加固及迟到视图错误处理源码。

[cc4a2fe / CI43555164134](https://github.com/wei-boop312/-AI-/actions/runs/43555164134)已整轮验证私有标注导出和本地中文标注页面：189单元/56契约/227原生/4普通+11受控浏览器及全部门槛通过。实际包1468文件、5,399,472字节，SHA f45c7e1f3c0451a35c98746bed800234ac24a58a322526f0cc2f112d777bb427；原始sidecar、三个前端专门证明与来源见ci-43555164134，下载后独立验证通过。后续运行配置/报价/登记候选不借用此包的绿灯。
