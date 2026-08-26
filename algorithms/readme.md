前端“执行视觉矫正”
→ 后端启动算法CLI
→ python -m algorithms.core.adjust_calibration
→ import core.adjust
→ _configure_adjust_runtime()
→ core.adjust._ensure_dependencies()
→ 导入 GroundingDINO
→ 尝试导入 groundingdino._C
→ _C 导入失败但被 warning 隐藏
→ 后续 CUDA 算子调用 _C
→ NameError: name '_C' is not defined
