# Python 静态检查

锁定Ruff 0.17.5与mypy 2.4.1（版本核对来源为[PyPI Ruff](https://pypi.org/project/ruff/0.17.5/)和[PyPI mypy](https://pypi.org/project/mypy/2.4.1/)），安装及传递依赖记录在uv.lock。运行：

```text
uv run python scripts/static_checks.py
```

Ruff格式及E4/E7/E9/F/I规则覆盖src/scripts/tests/migrations全部Python文件，脚本入口仅豁免因添加本独立src路径而产生的E402；其余规则实际执行。mypy对src/visual_platform与scripts全部82个源码分别使用Linux、Windows平台模型，开启check_untyped_defs、no_implicit_optional、冗余cast及无效ignore检查。不是AST解析，也不宣称所有函数参数已具备严格注解；动态JSON/数据库边界仍明确使用Any。仅asyncpg、boto3/botocore与PyYAML缺少上游类型信息的导入按具体模块豁免；自有模块不批量忽略。SQL迁移依赖实际PG执行，测试运行结果与静态检查分开验收。

本次统一格式/导入，修复不必要的跨类型变量复用、SSE字符串参数与内部整数游标混用、可空图像结果未检查、必须返回的数据库行未验证等问题。required_row只要求存在一行，保留行内NULL及原对象；不会把空标量当作无结果。图像解码子进程仍先设置资源限制，保留限制句柄。没有更改原始规格归档或SQL迁移内容。

本地80单元、39离线契约、两平台mypy及Ruff通过；本地报告static-local-checks.json明确implementation_dirty=true及基础提交，不能冒充该提交的CI证明。CI在干净checkout先执行该入口，交付门槛要求当前commit、干净实现、四个实际步骤全部成功、类型文件数与当前源码数匹配以及工具版本与精确锁定一致。尚待本次大幅格式整理后的原生PG/MQ/S3、浏览器、Compose及恢复CI回归。

新增工具的实际许可证文本/SBOM已更新：Windows构建环境94个Python、100个npm组件，277个法律文本引用，无缺失文本；当次Python/npm审计均无已知漏洞。这些范围包含开发工具，不等于基础OS/镜像的完整软件清单，也不保证不存在未知漏洞。
