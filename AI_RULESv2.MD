fish_quality_dashboard/
├── AI_RULES.md

```
）AI 开发约束文件，让后续 AI 修改代码时遵守你的习惯。

内容重点包括：

* 不随意修改架构
* 修改前先理解项目
* 前后端联调
* 数据库安全
* 前端必须真实浏览器验证
* 不执行 git
* 不删除旧页面
* 必须测试
* 必须报告修改文件
* 必须检查日志

---

```md
# AI_RULES.md

# 项目 AI 开发规则

本文件用于约束 AI 在 fish_quality_dashboard 项目中的代码修改行为。

所有 AI 修改代码前必须阅读本文件。

---

# 一、项目基本原则

## 1. 不允许盲目修改

任何修改前必须：

1. 阅读相关代码；
2. 理解当前架构；
3. 确认调用链；
4. 分析影响范围。

禁止：

- 看到报错直接改一行；
- 不理解上下游关系直接重构；
- 删除已有功能；
- 修改已有接口格式。

只允许保存：

- 正式源码；
- 配置文件；
- 必要资源；
- 项目运行必须文件。


禁止在项目目录生成：

- 测试脚本；
- 临时 Python 文件；
- CDP 自动化脚本；
- 截图；
- HTML dump；
- 测试日志；
- 验收报告；
- 临时 JSON；
- debug 文件。

## 2. 测试代码必须放项目外


所有测试辅助文件必须放到项目外，例如：


~/Code/yangzt/tmp/test/


或者：


/tmp/



例如：

浏览器 CDP 测试：


/tmp/cdp_test.py



接口测试：


~/Code/yangzt/tmp/test/api_test/



截图：


~/Code/yangzt/tmp/test/screenshots/



---

## 3. 测试过程中产生的数据禁止进入项目


禁止：

- 修改 data/
- 创建测试 results/
- 创建测试 jobs/
- 创建测试 uploads/


除非用户明确要求。


---

# 二、修改范围限制

默认原则：

## 只修改必要文件。

禁止：

- 大范围重构；
- 更换技术栈；
- 改数据库结构；
- 修改已有目录结构；
- 删除旧页面。


除非明确要求，否则：

不要：

- git commit
- git push
- git reset
- git checkout
- 删除文件


用户会自行管理 Git。


---

# 三、修改前必须检查

修改代码前必须确认：

## 后端

检查：

- FastAPI 路由
- service 调用关系
- 数据库模型
- schema
- 数据流


## 前端

检查：

- Vue 页面入口
- router
- component 引用
- API 调用
- 状态管理


## 算法

检查：

- pipeline 调用链
- 输入输出路径
- video_id 规则
- result 目录结构


---

# 四、数据库规则

数据库非常重要。

修改数据库相关代码时：

必须：

1. 查看当前表结构；
2. 查看已有数据；
3. 保护已有数据。


禁止：

- 随意执行 DELETE；
- 清空数据库；
- 修改生产数据；
- 创建重复记录。


测试前后必须比较：

例如：
```

video_task 数量
fish 数量
detection_report 数量
fish_id 是否重复

```

确认：

- 无重复数据；
- 无无关 video_id 被修改。


---

# 五、video_id 命名规则

当前正式规则：

```

YYYYMMDD_编号

```

例如：

```

20260726_0001
20260728_0002

```


禁止恢复旧格式：

```

video_20260723_184613_xxxxxx

```


所有：

- 文件路径
- 数据库字段
- API 参数
- 前端展示

必须兼容当前规则。


---

# 六、算法 Pipeline 修改规则


算法目录：

```

algorithms/

```

当前 Python 模块规范：

正确：

```

python -m algorithms.tools.xxx

```

禁止：

```

python -m tools.xxx

```


修改 pipeline 时必须检查：

```

calibrate
↓
video_to_frames
↓
perspective
↓
grounded_sam
↓
complete_filter
↓
measurement
↓
publish

```


任何 stage 修改后：

必须测试：

```

pipeline_state.json
pipeline_result.json
error_conclusion.txt

```


失败必须定位：

- failed_stage
- error_message
- stdout
- stderr


---

# 七、前端开发规则


## 1. 页面修改必须保持统一风格

不要随意新增：

- 特殊背景
- 新主题
- 新布局体系


必须遵循：

当前 AdminLayout 风格。


---

# 八、前端修改必须真实验证


只修改代码不算完成。

必须验证：

## 1. 页面加载

检查：

- HTTP 状态
- Vue 是否报错
- Console error
- Network error


要求：

```

JavaScript errors = 0
Vue warnings = 0
HTTP 4xx = 0
HTTP 5xx = 0

```


---

## 2. 浏览器真实测试

必须使用真实浏览器环境测试：

例如：

Chrome CDP。


检查：

- 页面是否显示；
- 点击是否有效；
- 路由是否正确；
- 弹窗是否打开；
- 数据是否更新。


不能只看：

- 编译成功；
- 接口返回成功。


---

# 九、前端交互测试要求


涉及按钮时必须测试：

例如：

按钮：

```

查看报表
视频明细
跳转大屏
刷新
查询
分页
删除
上传

```

必须验证：

点击前：

- DOM存在；
- 可点击。


点击后：

检查：

- URL变化；
- 页面变化；
- API请求；
- 数据变化。


---

# 十、页面数据验证


页面显示数据必须和数据库一致。


例如：

数据库：

```

fish=5
complete=0
incomplete=5

```


页面必须：

```

总鱼数 5
完整鱼 0
不完整 5

```


禁止：

- 写死测试数据；
- mock 数据替代真实接口。


---

# 十一、接口测试要求


修改 API 后必须测试：

例如：

```

curl
浏览器 Network
数据库查询

```


检查：

- status code
- 返回字段
- 空数据情况
- 异常情况


---

# 十二、空数据必须处理


所有列表页面必须考虑：

情况：

1. 有数据；
2. 无数据；
3. 请求失败。


例如：

无视频：

显示：

```

暂无视频

```


禁止：

空白页面。

---

# 十三、报告页面规则


PDF：

优先复用已有能力。


禁止：

重新开发新的 PDF 查看器。


要求：

验证：

- iframe
- preview API
- download API


PDF不存在：

必须提示：

```

当前视频暂未生成PDF报表

```


---

# 十四、修改完成后的输出格式


AI 完成修改后必须输出：

## 1. 修改文件列表

例如：

```

修改：

frontend/src/views/DataInput.vue

algorithms/tools/run_video_pipeline.py

```


## 2. 修改原因

说明：

- 原问题；
- 修改方案。


## 3. 测试命令

必须给出：

例如：

```

python -m algorithms.tools.run_video_pipeline --help

npm run build

curl xxx

```


## 4. 测试结果

说明：

成功：

```

页面正常
接口正常
数据库无变化

```


失败：

说明：

- 当前失败点；
- 下一步需要什么。


---

# 十五、不要自行扩大需求


用户提出一个问题时：

只解决该问题。


禁止：

- 顺便优化其他代码；
- 重构旧模块；
- 修改 UI 风格；
- 修改数据库设计。


---

# 十六、遇到不确定必须询问


如果缺少：

- 文件；
- 日志；
- 数据库结构；
- 页面截图；

必须先请求资料。


不要猜。


---

# 十七、项目验证优先级


任何功能完成顺序：

```

1. 服务启动正常
2. 数据库正常
3. API正常
4. 页面加载正常
5. 浏览器交互正常
6. 真实业务流程测试
7. 数据一致性检查

```


---

# 十八、最终目标

所有修改必须满足：

```

能运行
+
数据正确
+
页面正常
+
交互正常
+
无隐藏错误
+
不破坏已有功能

```

代码修改不是完成。

通过完整验证才算完成。
```
