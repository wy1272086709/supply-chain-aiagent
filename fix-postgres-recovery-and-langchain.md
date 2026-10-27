# 修复记录：数据库 recovery mode 报错 + 两个 AI 报错

> 2026-10-19。这次日志里其实混了**三个**问题，逐个说清楚。上一篇见 [fix-db-connection-closed.md](fix-db-connection-closed.md)（上传 PDF 500 那次）。

## 问题 A（主犯）：`the database system is in recovery mode`

### 现象

登录接口 500，报错 `the database system is in recovery mode`。

### 这句话什么意思

Postgres 遭遇了**非正常关闭**（没走完正常停机流程），重启后要重放 WAL 日志（预写日志）把数据恢复到一致状态，这个过程叫**崩溃恢复（crash recovery）**。恢复期间拒绝新连接，新来的连接就收到这句报错。

关键认知：**这是 Postgres 的自愈机制在正常工作，不是数据坏了。**

### 排查（证据链）

| 证据 | 结论 |
|---|---|
| 翻容器日志，`recovery in progress` 出现 **26 次** | 从 10-16 开始 Postgres 反复崩溃，不是偶发 |
| 每次死前**一行日志都没有**（没有 shutdown、没有 panic） | 被 SIGKILL 直接干掉的特征——进程没机会说话 |
| 容器本身 `restart: always` 没重启过、RestartCount=0 | 不是 docker 层面的问题 |
| 数据库超时配置全是 0、无慢查询凶手 | 不是数据库自己作的 |
| Docker VM 总内存 7.65G，ES+Kibana 等常驻 ~4.8G | **嫌疑一：VM 内存峰值时 OOM killer 杀进程** |
| 数据目录是 bind mount（`./volumes/postgres`） | **嫌疑二：macOS virtiofs 挂载 PG 数据目录是已知坑** |

真凶无法 100% 定罪（两个嫌疑都成立），但两个都有对策，见下面"修复"。

### 修复了什么

**1. healthcheck 查错了库（已修，已生效）**

[docker-compose.yml](docker-compose.yml) 里 postgres 的健康检查原来写的是：

```yaml
test: ["CMD-SHELL", "pg_isready -U user -d hello_pg"]   # ❌ hello_pg 这个库根本不存在
```

`hello_pg` 大概率是从别的教程/项目抄来的库名。后果：每 5 秒在 PG 日志里刷一条 `FATAL: database "hello_pg" does not exist`（你日志里那一堆 FATAL 就是它，不是外部攻击）。已改为 `-d knowledge_hub` 并重建了容器，噪音归零，数据完好（8 条文档都在）。

**2. 缓解崩溃本身（建议做，按需选）**

- **停掉不常用的辅助容器**（省 ~1.4G）：`docker compose stop kibana redisinsight pgadmin mongo_express`——不用它们的界面时关掉，ES 峰值时不容易触发 VM 内 OOM。宿主机 32G 内存，也可以把 Docker Desktop 的内存上限从 8G 加到 10-12G。
- **数据目录从 bind mount 迁到 named volume（可选加固）**：macOS 上把 PG 数据放 bind mount（virtiofs）有已知的稳定性问题。迁移步骤：
  ```bash
  docker exec knowledge_hub_postgres pg_dump -U user knowledge_hub > backup.sql   # 先备份！
  # docker-compose.yml 里把 ./volumes/postgres 那行换成 named volume，如 kh_pg_data:/var/lib/postgresql/data
  # 顶部加 volumes: kh_pg_data:
  docker compose up -d postgres
  cat backup.sql | docker exec -i knowledge_hub_postgres psql -U user -d knowledge_hub
  ```

### 应用侧为什么"撞上"这个错（但不用改代码）

- 应用连接池配了 `pool_pre_ping=True`：池里的旧连接死了会被自动换新，**这部分是自愈的**；
- 你撞上的那次是恢复窗口期（通常 1~7 秒）里**新建**连接——这种天然躲不掉，等几秒重试请求即可；
- 把上面第 2 点做完、崩溃不再发生，这个错就基本见不到了。

## 问题 B：DashScope 401 `invalid_api_key`

```
401 - Incorrect API key provided
```

原因很直接：[.env](.env) 第 57 行是**占位符**：

```
OPENAI_API_KEY=sk-xxx    ← 不是真实 key
```

配置解析顺序是 `OPENAI_API_KEY → LLM_API_KEY → DASHSCOPE_API_KEY`（[config.py:135](app/core/config.py#L135)），所以把真实的 DashScope key 填到 `OPENAI_API_KEY` 或取消注释 `DASHSCOPE_API_KEY=` 并填值都行。**这个只能你自己填，我没法替你填。** 注意 embedding / rerank 的 key 也走类似回退链（[config.py:147-151](app/core/config.py#L147-L151)），如果 Embedding 服务也用 DashScope，确认同一个 key 可用。

## 问题 C：`tool() got an unexpected keyword argument 'name'`

### 原因：langchain 0.x 写法跑在 1.x 版本上

venv 里装的是 **langchain-core 1.6.5**（1.x 大版本），1.x 里 `tool()` 装饰器把 `name` 关键字参数**去掉了**（改成第一个位置参数 `name_or_callable`）。而 [agent.py](app/modules/ai/agent.py) 里四处工具都是 0.x 的写法 `@tool(name="retrieve_knowledge", ...)`。

### 修复（已改，已验证）

```python
# 改前（0.x 写法，1.x 直接 TypeError）
@tool(name="retrieve_knowledge", description=..., args_schema=...)

# 改后（名字传位置参数）
@tool("retrieve_knowledge", description=..., args_schema=...)
```

顺带修了同一来源的第二个兼容问题——`SummarizationMiddleware` 的参数格式也变了：

```python
# 改前：trigger={"messages": 12}, keep={"messages": 6}      ← 字典
# 改后：trigger=("messages", 12), keep=("messages", 6)       ← 元组
```

验证方式：离线构建 Agent 成功（返回 `CompiledStateGraph`，不联网、不耗 token）。但**完整链路要等 B 的 key 填好才能真正跑通**——key 是占位符的话，Agent 能建起来，一调用还是 401。

## 改动清单

| 文件 | 改动 |
|---|---|
| [docker-compose.yml](docker-compose.yml) | healthcheck 库名 `hello_pg` → `knowledge_hub`，容器已重建生效 |
| [agent.py](app/modules/ai/agent.py) | 4 处 `@tool(name=...)` → `@tool("...")`；SummarizationMiddleware 参数字典 → 元组 |
| [.env](.env) | **待你操作**：第 57 行填真实 DashScope key |

## 一句话总结

> 日志里三个问题三个层次：**数据库在自愈**（recovery 是机制不是事故，要治的是"为什么崩"）、**key 是占位符**（401 不背锅）、**langchain 升了 1.x 代码没跟上**（`name=` 进了 1.x 的删除清单）。
