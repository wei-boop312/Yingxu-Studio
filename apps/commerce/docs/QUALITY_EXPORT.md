# 私有人工标注包

`python scripts/export_quality.py --user <用户UUID> --task <任务UUID> --check <报告UUID> --dataset-kind synthetic_fixture --output work/private-quality/review-001`

先创建被Git忽略的私有父目录`work/private-quality`。服务端环境使用现有非owner `ops_admin`的ADMIN_DATABASE_URL及私有S3配置；命令不读取模型密钥。输出目录必须尚不存在，只允许在本工程`.local`或`work`内；不要将样本或私有目录上传GitHub。

0047只授予Ops精确读取单个用户/任务/检查的函数，不开放业务表SELECT给API或Worker。必须是任务当前最终图的既有成功报告；错误用户、无关报告、未成功任务均拒绝。导出先关闭PG事务，再按精确版本下载私有PNG/报告，校验原始SHA、PNG尺寸、字节数、报告Facts和冻结平台范围，保留原文件字节。不同存储桶/版本/摘要、链接目录、路径逃逸和已有目录拒绝；验证失败不创建输出目录。文件写入失败时可能保留私有不完整目录，但manifest最后写，不覆盖重试。

包内`final.png`、`report.json`是原始字节；`provenance.json`保留私有任务/检查关联和来源模式，不包含S3地址或凭据。`manifest.json`与评测工具字段兼容，但human_label、annotator_fingerprint和所有人工特征为空，两个声明为false，不能直接通过质量评测。不要把自动结果复制为人工标签，也不自动声明素材权属。

可直接用浏览器打开包内`annotate.html`，中文界面展示最终图及各目标范围，不展示自动结论。四项同图物理特征共用，类目例外和人工合规结论分别按目标填写；标注者使用64位小写SHA256指纹。所有标签及两个声明完整后才允许下载`manifest.reviewed.json`。把下载文件移回同一包目录，再运行`python scripts/evaluate_quality.py --manifest work/private-quality/review-001/manifest.reviewed.json`。原始空白manifest不覆盖；页面不上传图片，不请求外部服务，不自动保存到云端。

页面使用静态内嵌资源和基于摘要的CSP，禁止连接与表单提交；范围文本以纯文本显示、内嵌JSON转义HTML边界。本地Chrome受控回归检查空白/声明门槛、来源字段不变、恶意范围文本不执行、无外网请求和原始清单不变；CI额外绑定生成器及测试源码SHA，不能用其他提交的证明替代。

人工逐项查看图片及站点/类目/图片用途/规则版本，填写合规或违规标签、标注者SHA和全部特征，确认审核与权属后才设置声明，再运行`python scripts/evaluate_quality.py --manifest work/private-quality/review-001/manifest.json`。无法判定的样本先人工复核，不强填。PNG和报告不可修改，重新导出使用新目录。

Fake/mock来源只能导出为synthetic_fixture；real_product_set不能据此宣称真实模型质量。原报告来源不明的重检拒绝导出，不能从工作流名推断供应商。导出和评测均不调用模型、不结算费用、不更改业务报告，也不授予发布资格。Windows文件隐私依赖所在目录ACL，POSIX目录0700/文件0600。
