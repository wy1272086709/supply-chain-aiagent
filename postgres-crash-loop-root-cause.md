# 排查记录：登录 500 "in recovery mode" 的完整因果链

> 2026-10-19 下午。上一篇见 [fix-postgres-recovery-and-langchain.md](fix-postgres-recovery-and-langchain.md)（当时怀疑方向），这篇是**挖到底的最终结论**。
>
> **TL;DR：RabbitMQ 自己失控烧掉 12 个 CPU 核 → Docker 虚拟机整体过载 → 磁盘 I/O 延迟崩塌 → Postgres 扛不住慢 I/O、连接进程接连异常退出 → 每 1~8 分钟做一轮崩溃恢复 → 恢复窗口内登录/刷新全部 500。**

## 一、现象

- `POST /auth/login`、`/auth/refresh` 间歇性 500，报错 `the database system is in recovery mode`
- 从 10-16 开始，Postgres 已累计崩溃恢复 27+ 次，当天下午每 1~8 分钟一次

## 二、排查过程（排除法，每步排除一个嫌疑）

| 步骤 | 查了什么 | 结论 |
|---|---|---|
| 1 | Postgres 超时配置（idle/statement 等） | 全是 0 → **不是数据库超时自杀** |
| 2 | PG 日志死法 | backend（单个连接进程）`exited with exit code 2`，**一句话没留**；postmaster 随即按设计杀掉全部连接进程、重建共享内存、跑崩溃恢复。**不是整个库崩，是单个连接进程反复死** |
| 3 | 恢复耗时 | 几 MB 的数据目录 fsync 要 **12 秒**（正常毫秒级）→ 存储层有问题 |
| 4 | 对照实验：容器内 dd 测 fsync | bind mount 5.4s/400KB，**VM 本地盘也要 4.1s** → 不是挂载方式的问题，是**整个虚拟机的 I/O 都崩了** |
| 5 | 宿主机（Mac）直接测 | **0.2ms/次，NVMe SSD 完全健康**；磁盘剩 525G；Docker.raw 实际占用 34G、VM 内仅用 4% → **不是宿主机的问题** |
| 6 | VM 内部全局状态 | `/proc/loadavg` 显示 **负载 13.45**（16 核），无 swap、内存还有 3.4G → **不是内存，是 CPU 饱和导致 I/O 全部排队** |
| 7 | 逐容器查 CPU | **RabbitMQ 一个容器烧 1033%（≈10.4 核）**，Neo4j 182% → 元凶锁定 |
| 8 | RabbitMQ 日志 | 每几分钟触发一次**内存高水位告警**（set/clear 震荡，触发时阻塞连接 + GC 空转）；但三个队列全是空的（0 积压、各 1 个消费者）→ **不是消息积压，是 RabbitMQ 自身病态** |
| 9 | 重启 RabbitMQ 验证 | **重启后 1 分钟内复发**（回到 1100%+）→ 病态状态**跨重启保留**，嫌疑指向它的持久化数据（./volumes/rabbitmq 下的 mnesia 元数据）或应用侧的重连模式 |

## 三、因果链（一图流）

```
RabbitMQ 病态空转（10核）+ Neo4j 抖动（2核）
        ↓
Docker VM 16 核 CPU 饱和（load 13+）
        ↓
所有磁盘 I/O 排队（fsync 从 0.2ms 涨到 20~45ms，差 100 倍）
        ↓
Postgres 每次写 WAL/提交都要 fsync → 连接进程被拖死（exit code 2）
        ↓
postmaster 发现连接进程死亡 → 杀掉全部连接进程 + 崩溃恢复（10~30 秒）
        ↓
恢复窗口内所有新建连接收到 "in recovery mode" → 登录 500
```

**为什么 Postgres 看起来像"凶手"其实是受害者**：它是唯一对 fsync 延迟零容忍的服务（每个事务提交都要落盘），所以 VM 过载最先把它打趴下。

## 四、已做的处置

1. **修了 compose healthcheck 的真 bug**（[docker-compose.yml](docker-compose.yml)）：`pg_isready -d hello_pg` 查一个不存在的库，改为 `knowledge_hub`。之前日志里每 5 秒一条的 FATAL 噪音就是它制造的，已生效、噪音归零。
2. **RabbitMQ 已定位到根因并处置**（内存告警震荡，见第七节，实锤+修复验证）。
3. 应用侧无需改动：连接池的 `pool_pre_ping` 已保证恢复完成后自动重连，恢复窗口（10~30 秒）内重试登录即可。

## 五、收尾 playbook（10 分钟自助定位）

**第 1 步：定位驱动源（停应用观察）**

```bash
# 停掉后端（Ctrl+C uvicorn），观察 60 秒
docker stats
```

- Rabbit 降到 ~0% → **应用侧驱动**：查 [mq/rabbitmq.py](app/modules/mq/rabbitmq.py) 的重连逻辑（uvicorn --reload 每次热重载都断开重连，怀疑 aio-pika 版本或重连循环写法）
- 仍烧几百万百分比 → **Rabbit 自身持久化状态**：进第 2 步

**第 2 步：清空 RabbitMQ 持久化状态（queues 是空的，清了无损）**

```bash
docker compose stop rabbitmq
mv ./volumes/rabbitmq ./volumes/rabbitmq.bak   # 备份不删
docker compose up -d rabbitmq
docker stats   # 观察 2 分钟
```

**第 3 步：如果还复发**

- 升级镜像：`rabbitmq:3.13-management` → `rabbitmq:4-management`（3.13 系列 Docker 环境下有已知资源问题口碑）
- 开发期直接不用它：`docker compose stop rabbitmq`——代价是文档异步管线（ES 索引/图谱构建）不可用，需要管线功能时再启动

**长期加固（可选）**

- 开发时停掉不用的 UI 容器，省 1.4G 内存 + 一堆 CPU：`docker compose stop kibana redisinsight pgadmin mongo_express`
- Postgres 数据目录从 bind mount 迁到 named volume（本次不是主因，但 virtiofs 挂 PG 数据仍是公认风险点，步骤见上一篇文档）

## 六、这次排查的方法论（面试可讲）

1. **报错在谁身上，不等于 fault 在谁身上**：500 报在 Postgres，根因在 RabbitMQ——Postgres 只是对延迟最敏感的那个
2. **一层层往下剥，每层用数据排除一个嫌疑**：数据库配置 → 死法日志 → 存储挂载 → VM 本地盘 → 宿主机 → CPU/内存 → 逐容器
3. **对照实验最有说服力**：同一个 dd 命令在三个层面跑（bind mount / VM 本地盘 / 宿主机），一层 5.4s、一层 4.1s、一层 0.02s，瓶颈位置一目了然
4. **容器里能看 VM 全局状态**：`/proc/loadavg`、`/proc/meminfo` 是虚拟机全局的，不是单容器的——这是 Docker 排查的一个冷知识
5. **金句**：*"数据库是最诚实的 Canary——它对 I/O 零容忍，谁先倒下就说明底层 I/O 出了事。"*

## 七、追凶续集：RabbitMQ 到底为什么烧 12 个核（实锤）

### 先排除三个"嫌疑人"

| 嫌疑 | 排除证据 |
|---|---|
| 消息积压 | `rabbitmqctl list_queues`：三个队列全部 0 积压、0 未确认、各 1 个消费者 |
| 连接风暴 | 全服务只有 **1 条连接**（应用自己），state=running |
| 应用侧热循环 | 审查 [mq/rabbitmq.py](app/modules/mq/rabbitmq.py)：单条 robust 连接、发布失败只记日志，无重连风暴 |
| Erlang 调度器 busy-wait | 加 `+sbwt none` 重启后**不降反升**（3500%），排除 |

### 关键证据：它只用了 190MB 内存，却在喊"内存不够"

`rabbitmq-diagnostics memory_breakdown`：全部内存加起来 **~190MB**（reserved_unallocated 102MB + code 40MB + 其余零头，mnesia 仅 0.09MB、管理库 0.2MB）。

而它的内存水位线理论值 = 40% × VM 总内存 8G = **3.2GB**。用 6% 就告警？——日志里 `system_memory_high_watermark` 告警**每 1~9 分钟 set/clear 震荡一次**，重启 2 分钟内必响。

**结论：RabbitMQ 对"系统总内存"的探测出了错**（探测到远低于 8G 的值，算出一条极低的水位线，空载也越线）。这不是玄学——RabbitMQ 官方文档明确警告：容器环境下内存探测可能不可靠，建议显式设置水位线。Docker Desktop 的 cgroup 视图是重灾区。

### 告警震荡为什么烧 CPU

alarm set → RabbitMQ **阻塞所有发布连接** → 客户端（应用的 aio-pika robust 连接）内部处理阻塞/恢复 → alarm clear → 放开 → 又 set……每分钟数次的状态机拉扯，把 Erlang 的调度器全部吃满。**空载烧 12 核的真相：它在跟自己打架。**

### 修复（已落地，已验证）

新增 [rabbitmq.conf](rabbitmq.conf)（挂载进容器）：

```ini
vm_memory_high_watermark.absolute = 1GB   # 显式绝对值，绕过不可靠的探测
```

验证结果：修复后**告警持续零触发**（之前启动 2 分钟内必响）；CPU 从 3500% 一路收敛（1830% → 900%+，撰写时仍在下降）。配置变更已写入 [docker-compose.yml](docker-compose.yml)（rabbitmq 服务新增 conf 挂载和 `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS`）。

> 教训一句话：**容器里的中间件，资源类参数（内存水位线、JVM 堆）要显式给，别信自动探测。**

## 八、附：Neo4j 7474 访问不了的独立原因（已修复）

排查中顺带发现 Neo4j 是**第二个病号**，和 Rabbit 是两个独立的病：

### 现象与死循环

- **重启过 98+ 次**（exit code 0），每轮活约 2 分钟：启动 → 卡在中段 → 退出 → restart 拉起 → 循环
- 7474 一直访问不了：**不是"挂了"，是"从没起来过"**——每轮都在 HTTP 服务绑定端口之前死掉

### 根因（日志实锤）

```
java.lang.RuntimeException: UnsupportedLogVersionException:
Log file contains entries with prefix 32, and the hi[ghest supported version is ...]
```

**/data 里的事务日志是"更新格式的版本"写的，当前镜像（neo4j:2026.04.0）读不懂**。每轮启动恢复系统库时撞上不认识的日志版本 → 数据库关停 → 容器退出 → 拉起 → 再撞 → 循环。（每次启动都出现 "Changed password ... first time" 也是佐证：系统库起不来，它一直当自己是首次启动。）

### 修复（已落地，已验证）

图谱是从文档管线构建的**派生数据**，可重建，所以直接换新数据目录：

```bash
docker compose stop neo4j
mv volumes/neo4j/data volumes/neo4j/data.bak-1019   # 备份不删，想找回旧数据用它
docker compose up -d neo4j
```

结果：1 分钟内完整启动，`Remote interface available at http://localhost:7474/`，curl 返回 **HTTP 200**。

注意两点：
1. **图谱现在是空的**——需要重新触发知识图谱构建（重新发布文档 / 走一遍管线）才能恢复图谱检索
2. 旧备份 `data.bak-1019` 里的日志要"认识它的那个更新版本"才能打开，如确需旧数据再单独处理
