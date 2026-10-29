# API 与关键业务规则（第一阶段）

## 1. 网页路由

| 方法 | 路径 | 登录要求 | 说明 |
|---|---|---|---|
| GET/POST | `/register` | 否 | 注册用户 |
| GET/POST | `/login` | 否 | 登录 |
| POST | `/logout` | 否 | 退出 |
| GET | `/dashboard` | 是 | 当前用户问卷列表 |
| GET/POST | `/surveys/new` | 是 | 创建问卷 |
| GET/POST | `/surveys/<survey_id>/edit` | 是且为创建者 | 编辑问卷与题目 |
| POST | `/surveys/<survey_id>/publish` | 是且为创建者 | 发布并设置可选截止时间 |
| POST | `/surveys/<survey_id>/close` | 是且为创建者 | 关闭问卷 |
| GET/POST | `/survey/<public_id>`、`/survey/<public_id>/submit` | 依匿名设置 | 填写并提交问卷 |
| GET | `/surveys/<survey_id>/stats` | 是且为创建者 | 查看统计 |

## 2. JSON API

### 创建问卷
`POST /api/surveys`，需登录。

```json
{"title":"用户体验调查","description":"收集反馈","allow_anonymous":false}
```

成功返回 `201`，包含内部 `id`、公开 `public_id` 和初始 `status`。

### 保存题目
`PUT /api/surveys/<survey_id>/questions`，需问卷创建者登录。

```json
{
  "questions": [
    {"id":"q1","title":"选择 A 或 B","type":"single","required":true,"options":["A","B"],
     "jump_rules":[{"operator":"eq","value":"A","target_question_id":"q3"}]},
    {"id":"q2","title":"填写年龄","type":"number","required":true,"min_value":0,"max_value":120,"integer_only":true},
    {"id":"q3","title":"其他说明","type":"text","required":false,"min_length":0,"max_length":300}
  ]
}
```

### 发布
`POST /api/surveys/<survey_id>/publish`，需问卷创建者登录。至少有一道合法题目才允许发布。返回公开填写链接。

### 提交回答
`POST /survey/<public_id>/submit`，JSON 示例：

```json
{"answers":{"q1":"A","q3":"补充说明"}}
```

成功返回 `201`。若题目必答、题型、选项、长度或数字约束不满足，返回 `400` 与 `error` 信息。问卷不可填写返回 `410`。需要登录但未登录返回 `401`。

### 查看统计
`GET /api/surveys/<survey_id>/stats`，需问卷创建者登录。返回总提交数以及每道题的回答数、选项计数、文本答案或数字平均值。

## 3. 题型字段

| type | 必要字段 | 可选校验字段 |
|---|---|---|
| `single` | `options`（至少两个不同选项） | `required` |
| `multiple` | `options` | `min_select`、`max_select`、`required` |
| `text` | 无 | `min_length`、`max_length`、`required` |
| `number` | 无 | `min_value`、`max_value`、`integer_only`、`required` |

题目 `id` 在同一问卷中必须唯一。题目类型和选项由后端校验，不能仅依赖浏览器控件。

## 4. 跳转规则

每道题可配置 `jump_rules` 数组，按数组顺序检查，第一条满足条件的规则生效。支持操作符：

- `eq`：相等，适合单选和数字精确值。
- `contains`：答案包含指定值，适合多选答案。
- `gt`、`gte`、`lt`、`lte`：数字大于、大于等于、小于、小于等于。

示例：

```json
"jump_rules": [
  {"operator":"eq", "value":"A", "target_question_id":"q5"},
  {"operator":"eq", "value":"B", "target_question_id":"q3"}
]
```

`target_question_id` 必须指向本问卷中存在的题目。设置为 `null` 表示满足规则后结束问卷。没有规则匹配时按题目数组的下一个题目继续；最后一题之后结束。填写页面逐题展示并按规则跳转；后端提交校验也按同一跳转路径判断哪些题目属于本次填写路径，避免要求回答被跳过的必答题。

## 5. 状态与校验

- `draft`：草稿，不能公开填写。
- `published`：发布状态；未到截止时间时可填写。
- `closed`：已关闭，不可填写。
- 截止时间到达后即使状态仍为 `published`，也视为不可填写。
- 所有答案在写入前由 `validate_answers` 重新校验。前端限制只改善体验，不是安全边界。
- 当前不限制同一用户多次提交，符合初始需求。


## 6. 第二阶段题库与版本功能

当前页面入口：`/question-bank`；题目系列详情：`/question-bank/families/<family_id>`。

- `POST /question-bank/new-question`：保存单题 JSON，创建初始版本，并自动加入个人“我的题库”。
- `POST /question-bank/new-bank`：创建命名题库。
- `POST /question-bank/add-to-bank/<version_id>`：将可访问的具体版本加入自己的题库。
- `POST /question-bank/<family_id>/share`：按用户名共享题目系列。
- `POST /question-bank/<family_id>/versions/<version_id>/new`：根据当前版本创建不可变新版本。
- `POST /question-bank/<family_id>/restore/<version_id>`：根据旧版本创建新的恢复版本。
- `POST /surveys/<survey_id>/add-library-question/<version_id>`：将特定版本复制为草稿问卷里的题目快照。
- `POST /surveys/<survey_id>/copy-draft`：复制问卷为新的草稿，用于修改发布后冻结的题目。

版本统计和问卷使用情况通过题目系列详情页查看。分享用户可使用题目，但不能修改原作者的版本历史。已发布问卷的题目不能通过网页或题目 API 修改。题库模板不保存 `jump_rules`，加入具体问卷后再配置跳转。
