# 阿里云 ECS 部署指南

## 架构

应用本身打成 Docker 镜像（根目录 `Dockerfile`），和生产版中间件一起由
`docker-compose.prod.yml` 编排。与开发版 `docker-compose.yml` 的差异：

| | 开发版 | 生产版 |
|---|---|---|
| GUI（pgadmin / mongo-express / redisinsight / kibana） | 有 | **去掉**（省 1G+ 内存） |
| 中间件端口 | 全部发布到宿主机 | **不发布**，仅内部网络互访 |
| 对公网端口 | — | 仅 `3000`（应用）和 `9000`（RustFS 文件直链） |
| Neo4j apoc 插件 | 有 | **去掉**（代码未使用，且其首次启动要从 GitHub 下载，国内易失败） |
| app | 宿主机 uv 运行 | 容器运行，依赖就绪（healthcheck）后自动启动 |

## 服务器要求

- 系统：Ubuntu 22.04 / 24.04 或 Alibaba Cloud Linux 3（脚本自动识别）
- 规格：推荐 **4C8G**；2C4G 可跑（脚本会自动加 2G swap 兜底），但 ES + Neo4j 偏紧
- 磁盘：≥ 40G（镜像约 8G + 文档/索引数据）
- 安全组（控制台 → ECS → 安全组 → 入方向）放行：
  - `22/tcp`（SSH）
  - `3000/tcp`（应用）
  - `9000/tcp`（RustFS 文件直链，PDF 抽图等 URL 要让浏览器访问到）

## 部署步骤

### 1. 本地打包（Mac，项目根目录）

```bash
bash deploy/pack.sh
# 产物 dist/knowledge-hub-<时间戳>.tar.gz
# 打包当前工作区（无需先 commit）；自动排除 .env / volumes / demo / .venv / 日志等
```

### 2. 上传并一键部署

```bash
scp dist/knowledge-hub-*.tar.gz deploy/deploy.sh root@<服务器IP>:/root/
ssh root@<服务器IP>
bash /root/deploy.sh
```

脚本会自动完成：装 Docker（阿里云镜像源）→ 配 Docker Hub 加速 → 小内存加 swap
→ 解压代码到 `/opt/knowledge-hub` → 生成 `.env`（自动填公网 IP、随机 JWT 密钥）
→ 构建镜像 → 启动 → 等待健康检查。

首次构建约 5-15 分钟（Python 依赖 + ES 装 IK 插件）。

### 3. 填 API Key

```bash
vim /opt/knowledge-hub/.env      # OPENAI_API_KEY（DashScope 兼容）等
cd /opt/knowledge-hub && docker compose -f docker-compose.prod.yml up -d app
```

不填 Key 时 RAG 向量化 / KG 抽取 / 对话生成降级，其余功能正常。

### 4. 验证

```bash
curl http://localhost:3000/          # 服务器上
open http://<服务器IP>:3000/docs     # 本地浏览器看接口文档
# 种子账号：admin / reviewer / user，密码均 123456
```

## 日常运维

```bash
cd /opt/knowledge-hub
alias dc='docker compose -f docker-compose.prod.yml'

dc ps                       # 状态
dc logs -f app              # 应用日志
dc logs -f es               # 某个中间件日志
dc restart app              # 重启应用
dc down                     # 停止全部（数据保留在 volumes/）
dc down -v                  # ⚠️ 连同网络一起删（volumes/ 在宿主机目录，不会删）
```

## 更新代码重新部署

```bash
# 本地：改代码 → commit → 打包上传
bash deploy/pack.sh
scp dist/knowledge-hub-*.tar.gz root@<服务器IP>:/root/

# 服务器：重跑脚本（解压覆盖代码，.env 与 volumes/ 数据保留）
bash /root/deploy.sh
```

也可以走 git（需先把部署文件 commit + push 到 GitHub）：
`cd /opt/knowledge-hub && git pull`，然后
`docker compose -f docker-compose.prod.yml build && docker compose -f docker-compose.prod.yml up -d`。

## 数据与备份

所有持久化数据在 `/opt/knowledge-hub/volumes/`（postgres / mongo / redis /
rabbitmq / es / neo4j / rustfs）。备份：停服后打包该目录，或用各库自己的导出工具。

## 常见问题

- **拉镜像超时 / `toomanyrequests`**：编辑 `/etc/docker/daemon.json` 的
  `registry-mirrors`（换成阿里云容器镜像服务控制台里你的个人加速地址），然后
  `systemctl restart docker`，重跑 `deploy.sh`。
- **应用没起来**：`docker compose -f docker-compose.prod.yml logs app --tail 100`；
  大概率是还在等某个中间件健康检查（首次 ES/Neo4j 要 1-2 分钟）。
- **ES 起不来（内存）**：2C4G 机器确认 swap 已生效（`free -h`），或把
  compose 里 `ES_JAVA_OPTS` 降到 `-Xms256m -Xmx256m`。
- **文件直链打不开**：检查安全组 9000 端口，以及 `.env` 里
  `RUSTFS_PUBLIC_URL` 是否为 `http://<公网IP>:9000`。
- **改了代码没生效**：`pack.sh` 打包的是运行那一刻的工作区——确认改动已保存并重新打包上传，服务器不会自动拉取新代码。
