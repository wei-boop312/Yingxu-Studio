# 本机验收样本副本

真实商品原图不进入GitHub。超过项目4MP输出或像素保护范围时，可在明确允许缩小后准备独立副本：

```text
python scripts/prepare_provider_sample.py --source <本机原图路径> --output-dir .local/new-sample --max-edge 2000 --allow-resize --dataset-kind real_product_source
```

命令只在被忽略的.local或work下创建全新目录，绝不覆盖原图或既有样本。未给--allow-resize而需要缩小时拒绝；不裁切、不放大。资源受限子进程先执行既有规范化，再以LANCZOS等比例缩小，输出仍需满足百炼输入和项目输出边界；不兼容的宽高比不会被静默拉伸或裁切。子进程环境不含模型/数据库/存储密钥，不联网。

source.png是新的像素保护基准，sample.json记录原文件/规范原图/副本各自SHA、前后尺寸和处理方式。缩小后的像素不是3000原图的逐像素原值，后续保护证明必须绑定新source_sha256。prepare阶段始终model_output_exists=false、pixel_protection_verified=false、quality_evaluation=NOT_RUN，不自动生成质量指标或创建数据库任务。合成测试使用--dataset-kind synthetic_fixture，不能冒称真实商品。

本轮用户明确允许将3000×3000商品原图制作2000×2000副本。已在本机私有目录完成，原图SHA保持不变；再以该副本和合成目标验证3个保护区域的精确回贴及单像素篡改拒绝。证明仅适用于本地保护算法，不证明真实模型出图、视觉质量或平台上架；图片与具体保护坐标/证明留本机，不上传仓库。
