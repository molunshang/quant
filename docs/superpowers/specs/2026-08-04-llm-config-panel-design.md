# LLM 配置管理面板 设计文档

日期：2026-08-04
状态：已批准（分节评审通过）

## 背景与问题

当前 LLM provider 通过手改 `config/llm.json` 配置（字段：`name`、`type`、`base_url`、`model`、`api_key`，`api_key` 支持 `env:VAR` 引用）。手改 JSON 容易产生语法错误与配置错误；且 `load_providers()` 在服务启动时只加载一次，改动需重启才生效。

目标：提供一个**单独的配置功能**（Web 设置面板 + 校验 API），让用户通过表单管理 provider，避免直接修改 JSON 出错，且写后无需重启即生效。

## 架构

新增 `ProviderConfigStore`（`api/agent/config.py`），作为 `config/llm.json` 的唯一读写入口：

```
config/llm.json  ← 唯一配置源（由应用写，不再手改）
        ▲ read/write（校验 + 原子写）
ProviderConfigStore
  · list / get / add / update / delete
  · set_default / get_default
  · test(provider) → 真实小调用
  · providers() → {name: LLMProvider} 缓存
  · 任何写入成功后 reload()，免重启生效
        ▲ 被 api/agent/api.py 使用（替换 load_providers()）
```

- JSON 仍是唯一配置源（符合现有约定与 `.gitignore`/文档），但只由应用写入。
- `load_providers()`（`api/agent/provider.py`）保留，`ProviderConfigStore.providers()` 内部复用它从配置构建 provider 实例，避免重复构建逻辑。`api/agent/api.py` 不再直接调用 `load_providers()`，改为经 `ProviderConfigStore` 获取（即「替换 api.py 里的调用点」）。
- 现有 `/api/providers`（返回名字列表，聊天下拉用）保持行为不变。

## 数据模型

`config/llm.json` 格式（向后兼容现有文件，新增顶层 `default`）：

```json
{
  "default": "anthropic-local",
  "providers": [
    {"name": "anthropic-local", "type": "anthropic",
     "base_url": "http://127.0.0.1:3456", "model": "opengo/deepseek-v4-flash",
     "api_key": "env:ANTHROPIC_API_KEY"}
  ]
}
```

- 顶层 `"default"`：默认 provider 名，聊天未指定时使用。
- `api_key`：明文 或 `env:VAR` 引用，两种形式均支持（保持现有能力）。

## 校验规则（保存前）

1. `name` 非空且唯一（大小写敏感）。
2. `type ∈ {anthropic, openai_compat}`。
3. `openai_compat` 必须带 `base_url`。
4. `model` 非空。
5. `api_key` 非空。

校验失败：返回中文明确错误，**不写文件**。

## API 端点（非 RESTful，路径直接体现功能）

全部挂到现有 `register_agent_routes`，复用 `ProviderConfigStore` 单例：

| 方法 | 路径 | 功能 |
|------|------|------|
| `GET` | `/api/llm/providers/list` | 返回 provider 列表 + `default` 名 |
| `POST` | `/api/llm/providers/add` | 添加 provider（校验+写盘+重载） |
| `POST` | `/api/llm/providers/update` | 编辑 provider（body 带 `name`；校验+写盘+重载） |
| `POST` | `/api/llm/providers/delete` | 删除 provider（body 带 `name`；默认项被删则清空 default） |
| `POST` | `/api/llm/providers/test` | 测试连接（body 传完整 provider 配置，真实调用一次，报告成功/失败） |
| `POST` | `/api/llm/default/set` | 设置默认 provider（body 带 `name`） |

行为约定：

- 写端点：校验失败 → `400` + 中文 `detail`；成功 → 返回最新列表。
- **测试连接**：端点**无状态**——body 传完整 provider 配置（`name/type/base_url/model/api_key`），既测已保存的行，也测表单里未保存的草稿。用该配置发极小真实请求（`max_tokens` 尽量小），成功回 `{ok: true}`，失败回 `{ok: false, error}`。`env:` 引用先展开再调用，可测出「环境变量未设/填错」。
- **写后即生效**：`add/update/delete/default-set` 成功后内存 provider 表立即重建，聊天/测试连接立即用新配置，无需重启。
- **聊天 provider 解析顺序**：`/api/chat` 请求体显式 `provider` → `default` 配置 → 第一个 provider（与现有行为兼容）。

## Web 面板（web/index.html）

新增「LLM 设置」面板（chat panel 之后）：

- **Provider 列表**：每行显示 `名称 / 类型 / 模型 / base_url`，右侧操作 `编辑`、`删除`、`测试连接`；行首 radio 标「默认」。
- **添加/编辑表单**：`名称`、`类型`（下拉 anthropic/openai_compat）、`base_url`、`model`、`api_key`（`env:XXX` 或明文）、`设为默认` 勾选。
- **测试连接**：点击后行内显示 `✓ 连接成功` 或 `✗ 错误信息`。
- **删除确认**：`confirm()` 确认后再删，防误删。
- 操作后重新拉取列表刷新 UI。

## 错误处理

- 所有写操作先校验后写盘；任何异常回 `400` 中文 `detail`。
- 配置文件损坏/不可解析时：读路径返回明确错误，不崩溃；写路径用原子写（先写临时文件再替换），避免写一半损坏文件。

## 测试

`tests/test_llm_config.py`（复用现有 pytest + TestClient 模式）：

- **ConfigStore 单测**（用 `tmp_path` 临时 json，不碰真实配置）：
  - 添加/编辑/删除/设默认，校验通过时正确写盘。
  - 校验失败（重名、非法 type、openai_compat 缺 base_url、空 model/key）→ 报中文错误且**不写文件**。
  - `default` 被删 → default 自动清空。
  - `env:` 展开；无 default 时回退到第一个 provider。
- **API 测试**（TestClient）：`list/add/update/delete/test/default-set` 端点状态码与返回体。
- **重载生效测试**：写一个 provider 后，`store.providers()` 立即反映新配置（无需重启）。
- **测试连接**：mock provider 层，不真发网络请求。
