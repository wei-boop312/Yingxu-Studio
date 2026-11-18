# 构建依赖、SBOM及第三方文本

当前工具链：uv0.13.7、Node24.22.1、pnpm11.25.0，开发工具pip-audit2.10.1、PyYAML6.0.3及jsonschema4.26.0均锁进uv.lock。`uv sync --locked`和`pnpm --dir apps/web install --frozen-lockfile`拒绝隐式更新。生产Python镜像不安装dev组；镜像及Actions引用分别固定digest和提交SHA。

`python scripts/dependency_artifacts.py`从实际安装的Python distribution和pnpm组件收集名称/版本/上游声明许可，输出[sbom](../sbom/)、[NOTICE](../third_party/NOTICE.md)、逐文件SHA及原始许可文本。CycloneDX 1.6通过官方库严格schema校验。清单标明当前操作系统，包含开发/测试工具；它不是全部锁文件平台变体或基础OS软件清单，也不是完整容器安全证明。CI会根据干净Linux环境重新生成并上传，不能拿Windows清单冒充Linux镜像清单。

缺少许可文件不会静默成功。现有补充资料包括：packageurl-python0.17.6已核对SHA的sdist中mit.LICENSE、icons-svg4.6.0 npm gitHead对应仓库根LICENSE；来源及摘要在[third_party/supplements/sources.json](../third_party/supplements/sources.json)。is-mobile的完整MIT许可位于已发布README，直接保留该文本；rolldown平台绑定使用同版本父包LICENSE及THIRD-PARTY-LICENSE，清单明确记录来源。不得据这些来源把所有依赖统称MIT/Apache；例如psycopg-binary声明LGPL-3.0-only、certifi声明MPL-2.0，原文随清单保留。

本项目自有代码未由生成脚本指定开放源代码许可。NOTICE记录上游声明与文本来源，不提供新的授权，也不宣称已完成全部二进制/基础OS再分发义务核验。两个应用镜像复制第三方文本，Python环境原dist-info法律文本也保留；生产发布前仍需核对相应源代码提供、替换/链接方式及完整镜像清单。

`python scripts/dependency_audit.py --output work/dependency-audit`调用pip-audit及pnpm audit，不自动fix、不忽略具体漏洞；在线源不可用、工具失败、跳过包、缺少可解析结果或已知漏洞均返回失败。摘要绑定两个锁文件SHA，查询结果只表示该时刻已知advisory，不能证明不存在未知漏洞。本机本轮88项Python已安装依赖、npm各级已知漏洞均0；证据dependency-audit-20261113.log。当前生成清单88 Python/100 npm、未缺法律文本，见dependency-artifacts-20261113.log。

CI以固定digest的Gitleaks8.30.1扫描全部提交历史并redact。35694a4完整CI/P5-01已通过，原始报告保留ci-43323370220；后续新增容器运行闭环仍待新CI。工作流只读仓库、构建/测试，不上传私有.local、运行凭据、JWT私钥或备份；构建产物与公开证据短期保存，不推镜像、不部署。

归档原始 docker inspect 后，generic-api-key 将 Python 基础镜像公开 GPG 签名指纹判为一条命中，导致43324941200扫描失败。已依据[官方 Python 镜像 Dockerfile](https://raw.githubusercontent.com/docker-library/python/master/3.13/slim-bookworm/Dockerfile)核对它用于下载 Python 签名公钥，不是私钥或 API 凭据。`.gitleaks.toml`继承全部默认规则，只为归档 docker-images.jsonl 路径 AND 该确切 GPG_KEY 匹配增加目标规则例外；不忽略文件、目录、提交或一般凭据。

使用[官方 Gitleaks8.30.1 Windows 发行包](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)，SHA同时核对 release asset digest 和官方checksums，实际扫描34个提交后无其他命中。4项范围实测：确切公钥/正确路径通过，同值其他路径、其他值同路径、其他凭据同路径均被检出。证据gitleaks-public-fingerprint-scope-20261113.json。CI显式指定同配置，并保存redacted JSON报告供失败定位；不会因为扫描失败就把默认规则关闭。

官方依据：[Docker多阶段构建](https://docs.docker.com/get-started/docker-concepts/building-images/multi-stage-builds/)、[GitHub构建产物](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)、[Gitleaks](https://github.com/gitleaks/gitleaks)、[pip-audit](https://github.com/pypa/pip-audit)、[CycloneDX 1.6](https://cyclonedx.org/docs/1.6/json/)。
