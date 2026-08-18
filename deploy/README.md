# StockPulse 部署与验证

本说明提供可重复的容器装配步骤，但本次代码任务只做本地验证，**实际服务器部署未执行**。默认端口方式仅适用于内网、VPN，或云安全组/主机防火墙已经限制来源 IP 的服务器。禁止把未认证的 8888 端口直接暴露到公网；公网访问必须另行配置 Nginx、HTTPS 和认证。

## 1. 前置条件

- Linux x86_64/arm64 服务器
- Docker Engine 24+ 与 Docker Compose v2
- 至少 2 GB 可用内存和 5 GB 可用磁盘
- 安全组只允许可信来源 IP 访问目标端口

## 2. 准备配置

```bash
cd /opt/stock-analysis-hub
cp .env.example .env
mkdir -p data uploads
chmod 700 data uploads
```

编辑 `.env`，至少复核以下配置：

```dotenv
STOCKPULSE_BIND_HOST=0.0.0.0
STOCKPULSE_PORT=8888
TRADING_ENABLED=true
TRADING_SCHEDULE_ENABLED=false
TRADING_TIMEZONE=Asia/Shanghai
TRADING_PROVIDER_PRIORITY=eastmoney,akshare
TRADING_JOB_LOCK_TTL_SECONDS=900
TRADING_STALE_JOB_SECONDS=1800
TRADING_SCHEDULER_POLL_SECONDS=30
```

如使用 OCR，再填写百度 OCR 密钥。不要提交 `.env`、数据库、证书、私钥或 htpasswd。

## 3. 本地验证

以下命令只验证配置解析、镜像构建和本机容器运行，不等于实际服务器部署：

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

`web` 必须显示 `healthy`，`scheduler` 必须保持 `Up`。Web 和 Scheduler 都通过 Compose 强制使用 `/app/data/stock.db` 与同一 `data` 卷；Web lifespan 和 Scheduler 启动都会先运行幂等交易迁移。Scheduler 等待 Web 健康后才启动。

在本机检查：

```bash
curl --fail http://127.0.0.1:8888/api/health
curl --fail http://127.0.0.1:8888/preview
docker compose logs --tail=200 web
docker compose logs --tail=200 scheduler
```

健康响应必须同时包含 `"db":"ok"` 和 `"trading_schema":{"status":"ok"}`。数据库损坏、迁移失败、交易表或关键列缺失时，服务启动失败或 `/api/health` 返回 503，不得继续接收交易请求。

## 4. 验证持久化任务闭环

把日期改成最近一个已收盘交易日：

```bash
JOB_JSON=$(curl --fail -sS -X POST \
  http://127.0.0.1:8888/api/trading/data-jobs \
  -H 'Content-Type: application/json' \
  -d '{"job_type":"update_bars","trade_date":"2026-07-31","stock_codes":["000001.SZ"]}')
echo "$JOB_JSON"
JOB_ID=$(python -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$JOB_JSON")
curl --fail -sS "http://127.0.0.1:8888/api/trading/data-jobs/$JOB_ID"
```

POST 只应创建 `QUEUED` 记录；Scheduler/DataJobService 消费后，GET 会展示 `attempts`、`result_json` 和 `error_json`。成功任务进入 `SUCCEEDED`；Provider 持续不可用时，最多 3 次后进入 `FAILED`，不能永久停在 `QUEUED`。

确认手工任务闭环后，才在 `.env` 中显式设置 `TRADING_SCHEDULE_ENABLED=true`，然后执行：

```bash
docker compose up -d scheduler
```

## 5. 实际服务器部署

实际服务器部署本轮**未执行**。默认 `docker-compose.yml` 保留 IP + 端口模式，
只适用于内网/VPN/受控安全组。在目标服务器上重复第 2、3、4 节，并从允许
访问的客户端验证：

```bash
curl --fail http://服务器IP:8888/api/health
```

确认服务器时区、数据目录权限、磁盘余量、Provider 出站网络和安全组规则。若服务器具有公网 IP 且无法限制来源，停止部署，不要开放 8888。

公网模式必须单独使用 `deploy/docker-compose.public.yml`，不要与默认 Compose
文件叠加。该文件的 `web` 没有宿主机 `ports`，只能由同一 Compose 网络内的
Nginx 访问；只有 Nginx 发布 80/443，并通过 HTTPS + Basic Auth 代理 Web：

```bash
mkdir -p deploy/certs
# 放置 deploy/certs/cert.pem 和 deploy/certs/key.pem
htpasswd -c deploy/htpasswd <username>
docker compose -f deploy/docker-compose.public.yml config
docker compose -f deploy/docker-compose.public.yml build
docker compose -f deploy/docker-compose.public.yml up -d
docker compose -f deploy/docker-compose.public.yml ps
curl --fail --user '<username>:<password>' https://服务器IP/api/health
```

启动前检查 `config` 输出：`web` 不得出现 `ports`，`nginx` 必须是唯一发布
宿主机端口的服务。不得同时运行默认 IP + 端口模式，否则会重新暴露未认证的
8888 绕过 Nginx。

## 6. 备份与恢复

数据库在线备份产物保存在宿主机 `data/backup/`。升级或迁移前，必须调用项目
的 SQLite online backup；禁止在 Web/Scheduler 仍写入时直接复制数据库、WAL
或整个运行目录：

```bash
BACKUP_JSON=$(docker compose exec -T web python -c \
  "import json; from backend.trading.jobs.backup_database import run_backup; print(json.dumps(run_backup('/app/data/stock.db', '/app/data/backup')))" )
echo "$BACKUP_JSON"
BACKUP_FILE=$(python -c \
  'import json,sys; r=json.load(sys.stdin); assert r["integrity_ok"] and r["integrity_result"] == ["ok"]; print(r["path"])' \
  <<<"$BACKUP_JSON")
BACKUP_NAME=$(basename "$BACKUP_FILE")
HOST_BACKUP="data/backup/$BACKUP_NAME"
(
  cd data/backup
  sha256sum "$BACKUP_NAME" | tee "$BACKUP_NAME.sha256"
  sha256sum --check "$BACKUP_NAME.sha256"
)
```

只有 `integrity_ok=true`、`integrity_result=["ok"]` 且 `sha256sum --check` 成功
后，才能把这个不可变 `.db` 产物及其 `.sha256` 文件复制到独立备份介质。不要
复制运行中的 `stock.db-wal`/`stock.db-shm` 作为升级恢复点。

公网模式执行同一流程时，把命令中的 `docker compose` 替换为
`docker compose -f deploy/docker-compose.public.yml`。复制到独立介质后，应在
备份文件所在目录再次运行 `sha256sum --check "$BACKUP_NAME.sha256"`。

恢复演练必须在停写窗口或隔离环境中进行：

1. `docker compose down` 停止 Web 和 Scheduler。
2. 保留故障数据库副本，不覆盖唯一备份。
3. 对独立介质上的备份再次执行 `sha256sum --check`。
4. 将已由 online backup 和 `PRAGMA integrity_check` 验证的 `.db` 复制为
   `data/stock.db`，不要混入旧 WAL/SHM。
5. `docker compose up -d web`，确认迁移完成且 `/api/health` 为 200。
6. 再启动 Scheduler，并检查 `docker compose logs` 与任务终态。

本轮未执行真实恢复演练，因此不能把文档步骤视为恢复成功证据。

## 7. 日常运维与停止

```bash
docker compose logs --tail=200 web
docker compose logs --tail=200 scheduler
docker compose restart
docker compose down
```

升级前重新执行 `docker compose config`，完成第 6 节 online backup、完整性和
checksum 验证，再运行 `docker compose build && docker compose up -d`。
