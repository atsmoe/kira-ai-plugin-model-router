# KiraAI 模型故障切换路由

为 KiraAI 的合并消息配置一个主模型和多个备用模型。当主模型遇到核心支持的 API 错误、超时或连接故障时，由 KiraAI 按顺序尝试备用模型。

支持同一提供商或跨提供商配置、按顺序去重，以及保留其他插件已设置的模型组。

> 插件只负责构造模型调用顺序，实际请求与故障切换由 KiraAI 核心执行。它不增加重试、不修改核心文件，也不会因为回答质量不佳而自动换模型。

## 版本与兼容性

当前插件版本：**1.0.4**。可直接从本仓库 `main` 分支安装或更新。

从 1.0.4 起，插件不声明 `core_version`，不对 KiraAI 设置最低版本、最高版本或精确版本限制。KiraAI 更新版本号时，不会仅因版本检查而拒绝加载本插件，也不需要为了放宽版本范围反复更新插件。

已核对的核心版本为 KiraAI [2.31.4](https://github.com/xxynet/KiraAI/commit/27b5273dc59983b6843de23f294dfb4dbc06ca5c) 和 [2.33.3](https://github.com/xxynet/KiraAI/commit/5424c7dffb750a46dabcd20db69ca31007be7780)。这些是验证记录，不是安装限制，也不代表保证兼容所有历史或未来版本。如果核心实际修改了插件依赖的接口，再根据具体问题修复。

旧版 1.0.2 锁定 2.31.4，1.0.3 限制在 2.31.4–2.33.3；更新到 1.0.4 即移除这些版本门槛，现有主备模型配置无需迁移。

## 快速配置

1. 在 KiraAI 中配置好主模型及备用模型对应的提供商和模型。
2. 通过 KiraAI 支持的插件管理方式安装兼容版本，并确认插件已启用、成功加载。
3. 在本插件配置中，将“主模型”设为 `default`，沿用 KiraAI 的默认模型。
4. 在“备用模型”列表中按尝试顺序填写模型引用，保存配置，并按当前插件管理流程重新加载以使配置生效。

以下均为占位示例，请替换为自己已经配置的提供商和模型：

```json
{
  "enabled": true,
  "primary_model": "default",
  "fallback_models": [
    "provider_b:model_backup_1",
    "备用服务商：model_backup_2"
  ],
  "max_chain_length": 5,
  "respect_existing_model_group": true
}
```

没有上游模型组时，上述配置按“默认模型 → 备用模型 1 → 备用模型 2”构造调用链。默认配置的备用列表为空，**仅安装插件不会自动发现或添加备用模型**。

### 模型引用怎么填写

支持两种形式：

- `provider_id:model_id`：提供商 ID 与模型 ID，适合长期配置。
- `提供商显示名:model_id`：精确显示名与模型 ID；也接受全角冒号 `：`。

分隔符两侧空白会被去除；显示名区分大小写，且必须唯一。提供商部分不能包含冒号；模型 ID 中的后续冒号会保留。主模型还支持特殊值 `default`，备用模型必须填写完整引用。

插件优先按提供商 ID 查找；找不到对应 ID 时才尝试精确显示名。显示名重复、模型不存在或配置格式无效时，会跳过该项，不会猜测或随机选择。若 ID 已存在但模型不可用，也不会再改用同名的其他提供商。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否为合并后的 IM 消息构造模型组；还需在 KiraAI 插件管理中启用插件。 |
| `primary_model` | `default` | 使用核心默认模型，或填写一个完整模型引用。 |
| `fallback_models` | `[]` | 按尝试顺序排列的备用模型引用，可跨提供商。 |
| `max_chain_length` | `5` | 插件构造的模型链长度上限，包含主模型；应为正整数。 |
| `respect_existing_model_group` | `true` | 保留上游插件设置的模型组，并在后面追加备用模型。 |

### 与其他路由插件一起使用

- 默认保留已有模型组：以已有模型组为链首，只追加本插件的备用模型，此时不会插入 `primary_model`。
- 关闭 `respect_existing_model_group`：用本插件解析出的有效模型链替换已有模型组；如果一个模型都无法解析，则保持原样。
- 按“提供商 ID + 模型 ID”去重，保留首次出现的位置。
- 不会为了满足较低的 `max_chain_length` 而截断上游已有模型组；已有组达到上限时不再追加备用模型。

本插件以 `Priority.LOW` 注册合并消息钩子。需要先设置模型组的上游插件应使用 `MEDIUM` 或 `HIGH`；多个 `LOW` 插件的执行先后依赖注册顺序，不保证本插件总是最后执行。

## 常见问题

### 升级 KiraAI 后插件加载失败

如果仍在使用 1.0.2 或 1.0.3，请先更新至 1.0.4，移除旧的版本限制。1.0.4 不会按核心版本号阻止加载；若仍然失败，应查看具体的导入、依赖或接口错误，而不是继续修改版本号。

### 为什么没有切换备用模型？

检查以下几点：

- 插件是否成功加载，且插件管理开关和配置中的 `enabled` 都已开启。
- `fallback_models` 是否非空，模型引用是否能匹配已配置的提供商和模型。
- 是否因为重复、链长度上限或上游模型组已满而未加入备用项。
- 是否发生了核心支持切换的异常。插件不对所有异常或低质量回答额外实现切换。

在已核对的核心实现中，`APIStatusError`、`APITimeoutError`、`APIConnectionError` 和 `ProviderAPIError` 可触发下一模型尝试；全部候选失败后的处理仍由核心负责。

### 日志出现一次模型错误，是否说明最终回复失败？

不一定。主模型出错后，备用模型仍可能成功。应结合后续调用结果判断；本插件不发送错误通知，也不负责最终成功或失败的通知文案。

### 找不到模型或提供商名称重复怎么办？

建议改用稳定的提供商 ID。插件会记录被跳过配置项的角色、位置和原因，不记录完整模型引用、服务地址、凭据或请求正文。若全部模型都无效，则保留原事件，让 KiraAI 继续正常路由。

## 开发与验证

在插件仓库根目录运行：

```bash
python -m unittest discover -s tests -v
python -m compileall -q main.py router.py tests
python -m json.tool manifest.json
python -m json.tool schema.json
git diff --check
```

单元测试覆盖主备顺序、跨提供商解析、全角冒号、显示名歧义、去重、长度上限、已有模型组保留、生命周期和钩子执行顺序。测试使用模拟上下文与模型客户端，不代表真实提供商调用一定成功。

主要核心接口（链接固定到已核对的 KiraAI 2.33.3 提交）：

- [插件钩子注册](https://github.com/xxynet/KiraAI/blob/5424c7dffb750a46dabcd20db69ca31007be7780/core/plugin/plugin_registry.py)：`@on.im_batch_message`。
- [消息处理](https://github.com/xxynet/KiraAI/blob/5424c7dffb750a46dabcd20db69ca31007be7780/core/message_manager.py)与[消息事件](https://github.com/xxynet/KiraAI/blob/5424c7dffb750a46dabcd20db69ca31007be7780/core/chat/message_utils.py)：在模型调用前读取 `event.model_group`。
- [插件上下文](https://github.com/xxynet/KiraAI/blob/5424c7dffb750a46dabcd20db69ca31007be7780/core/plugin/plugin_context.py)与[提供商目录](https://github.com/xxynet/KiraAI/blob/5424c7dffb750a46dabcd20db69ca31007be7780/core/provider/provider_manager.py)：解析默认模型、模型引用和显示名。
- [核心执行器](https://github.com/xxynet/KiraAI/blob/5424c7dffb750a46dabcd20db69ca31007be7780/core/agent/agent_executor.py)：执行有序模型链及异常切换。

## 使用边界

备用模型可能改变费用、延迟、上下文容量、工具调用和多模态能力，请配置适用于同一工作负载的模型。插件不提供健康评分、熔断、自动发现、基于回答质量的切换，也不增加核心之外的重试。
