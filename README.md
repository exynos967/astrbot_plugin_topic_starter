# Topic Starter 插件

AstrBot 主动话题发起插件，提供话题管理与会话追踪能力。

## 已迁移能力

- 主动话题发起：基于概率、静默时长、冷却时间与免打扰时段进行触发。
- 会话绑定与追踪：只对绑定会话生效，记录最近消息作为上下文。
- 话题管理：支持创建、列出、删除话题。
- 手动触发：可在当前会话立即触发一次主动发言。
- LLM 生成文案：可调用当前会话模型生成更自然的发言，失败时自动回退模板文案。

## 指令

- `/topic_help`
- `/topic_bind`
- `/topic_unbind`
- `/topic_status`
- `/topic_create 标题|描述`
- `/topic_list`
- `/topic_delete 话题ID`
- `/topic_initiate`

## 数据存储

使用 AstrBot 插件 KV 存储（`get_kv_data/put_kv_data/delete_kv_data`）：

- `topics`: 话题定义与统计
- `streams`: 绑定会话与触发时间
- `messages`: 会话最近消息窗口

## 特别感谢(灵感来源)

- [ARC](https://github.com/A-Dawn) 本插件参考了他的A_Mind插件