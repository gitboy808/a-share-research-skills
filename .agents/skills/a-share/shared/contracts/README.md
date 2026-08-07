# 版本化任务契约

此目录保存进入各类工作流前的证据底线。运行清单可以实例化契约并增加条件，但不能删除 `required_evidence` 中的基础要求。契约只定义输入条件，不保存事实、判断或检索结果；修改证据底线属于 L3 变更。

当前内置契约使用 `a-share-task-contract-v1`，并由工作集装配 module 读取。需要对象专属条件时，通过运行清单传入 `task_contract` 和 `strategy_version`，不要把条件写入 skill 提示词。
