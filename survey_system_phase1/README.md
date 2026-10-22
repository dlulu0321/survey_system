# 基于MongoDB的问卷系统（第一阶段）

## 1. 功能清单

- 用户注册、登录、退出；密码使用Werkzeug的安全哈希，不保存明文密码。
- 用户只能查看、编辑和统计自己创建的问卷；填写者通过 /survey/<public_id> 访问。
- 创建问卷，填写标题、说明、匿名策略；题目 JSON 可编辑；发布、截止时间、关闭。
- 支持single、multiple、text、number。
- 支持必答、多选最少/最多选择数、文本最短/最长长度、数字上下界、整数限制。
- jump_rules存在问卷题目文档中，前端逐题导航并由后端重复验证跳转路径上的答案。
- 保存每次提交；支持匿名提交开关；统计单选/多选计数、文本回答、数字平均值。
- 提供JSON API与unittest测试。

## 2. 环境准备

需要Python 3.10+和正在运行的MongoDB。Windows上请先启动MongoDB服务，再执行以下命令。

```powershell
cd survey_system_phase1
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑.env，将SECRET_KEY改为随机长字符串，并确认MONGODB_URI与MongoDB服务地址一致。

```powershell
python app.py
```

浏览器打开http://127.0.0.1:5000。首次运行时应用会创建需要的索引。生产部署必须使用随机SECRET_KEY、HTTPS，并将COOKIE_SECURE=true。

## 3. 创建问卷的步骤

1. 注册账号并登录。
2. 点击“新建问卷”，填写标题和说明，选择是否允许匿名填写。
3. 创建后进入编辑页面，在题目 JSON 文本框中配置题目。一个有效题目示例：

```json
{
  "id": "q1",
  "title": "你更喜欢哪种水果？",
  "type": "single",
  "required": true,
  "options": ["苹果", "香蕉", "西瓜"],
  "jump_rules": [
    {"operator": "eq", "value": "苹果", "target_question_id": "q3"}
  ]
}
```

4. 保存题目并发布。系统会生成类似/survey/随机公开ID的填写链接。
5. 填写者提交后，问卷创建者可以在“统计”页面查看统计结果。

完整题型和跳转字段说明见docs/API与规则说明.md。

## 4. 测试

```powershell
python -m unittest discover -s tests -v
```

测试使用mongomock模拟MongoDB，测试数据不会写入真实数据库。测试用例及测试记录模板见docs/测试用例.md。

## 5. 目录结构

```text
app.py                   Flask 路由、账号、问卷工作流和 JSON API
db.py                    MongoDB 连接与索引
schema.py                用户、问卷、回答文档结构
logic.py                 题目校验、跳转路径、统计计算
static/                  CSS 和逐题跳转脚本
templates/               简单网页模板
tests/                   自动测试
 docs/                   数据库设计、API、测试和项目报告
.env.example             环境变量示例
requirements.txt         Python 依赖
```

## 6. 第一阶段范围与限制

- 当前题目编辑器使用JSON配置，优先保证题型和规则表达清楚；不是完整的拖拽式问卷设计器。
- 跳转规则按数组顺序匹配第一条满足的规则；没有规则匹配时按题目顺序继续，匹配到target_question_id: null时结束问卷路径。
- 匿名策略在本实现中表示：允许未登录用户提交，且其respondent_id保存为null。未勾选匿名时，填写者必须登录。
- 同一用户可以重复填写；初始需求没有要求防止重复提交，因此不额外限制。
- 统计由已保存的回答记录实时计算，不维护易产生不一致的预聚合统计值。
- 这是课程原型。生产环境还应加入CSRF防护、登录限流、审计日志、邮件找回密码等安全与运维能力。
