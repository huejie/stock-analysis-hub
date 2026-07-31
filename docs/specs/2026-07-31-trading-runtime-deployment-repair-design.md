# StockPulse 交易运行时与服务器部署修复设计

> 日期：2026-07-31
> 状态：已完成交互式设计确认，等待书面复核
> 依据：`docs/specs/2026-07-21-trading-decision-system-design.md` 与 2026-07-30 现状审计

## 1. 目标

本轮修复只解决交易决策模块的服务器运行闭环，使系统在空数据库或现有数据库上启动后具备以下能力：

1. Web 启动时自动、幂等地完成交易迁移。
2. Web 和 Scheduler 使用同一套真实行情 Provider 组合。
3. 手工数据任务返回 HTTP 202 后会被独立 Scheduler 领取并执行。
4. 定时行情、计划和备份任务按上海时区可靠执行，进程重启后可恢复。
5. Docker Compose 通过 `服务器 IP + 端口` 提供访问入口。
6. 健康检查能够发现交易迁移缺失，而不是只检查普通数据库连接。

## 2. 范围边界

### 2.1 本轮包含

- Web lifespan 交易迁移。
- 交易迁移健康检查。
- Provider 工厂与配置映射。
- 东方财富主源、AKShare 备用源的 Composite Provider 运行时装配。
- 数据任务原子领取、执行、重试、失败记录和重启恢复。
- Scheduler 到期补跑、上海时区、交易日判断和持久任务锁。
- Docker Compose 的 IP + 端口入口、健康检查和安全构建上下文。
- 运行与部署说明。
- 后端单元、集成和启动冒烟测试。

### 2.2 本轮不包含

- 市场宽度、评分、入场、退出、连续亏损和 T+1 计划项等策略公式整改。
- 回测与复盘前端。
- 全站统一认证、域名、TLS 证书或 Basic Auth。
- Celery、Redis、RabbitMQ、APScheduler 等新基础设施。
- 多用户、多 Scheduler 主动并行执行。

## 3. 部署安全边界

Compose 默认映射：

```text
${STOCKPULSE_BIND_HOST:-0.0.0.0}:${STOCKPULSE_PORT:-8888}:8888
```

该入口只适用于以下任一环境：

- 内网服务器。
- VPN 内服务器。
- 云安全组或主机防火墙已限制允许访问的来源 IP。

禁止把未认证的 8888 端口直接暴露到公网。需要公网访问时，必须另行启用 Nginx、HTTPS 和认证，不属于本轮范围。

## 4. 方案选择

### 4.1 采用方案

采用“运行时服务分层”：

- Web lifespan 负责迁移和启动前检查。
- Provider Factory 只负责把配置转换为 Provider 实例。
- Data Job Service 负责一个任务从领取到完成的完整状态变化。
- Scheduler Runner 负责时间、交易日、任务创建、领取和重启恢复。
- Repository 负责原子 SQL，不包含行情业务逻辑。

### 4.2 未采用方案

- 不在 `main.py`、`router.py`、`scheduler.py` 中继续堆叠所有逻辑，避免修复后仍难以测试。
- 不引入外部任务队列，避免增加单用户 SQLite 系统的部署复杂度。

## 5. 目标组件

### 5.1 Web Lifespan

位置：`backend/main.py`

启动顺序：

1. 执行 `run_migrations(settings.db_path)`。
2. 校验 `trade_migrations` 已包含当前全部版本。
3. 校验失败时抛出启动异常，拒绝提供“部分可用”的 Web 服务。
4. 成功后再开始接收请求。

迁移必须保持对空库和现有库的幂等性。Web 和 Scheduler 都可以调用迁移，但重复调用不得产生额外 schema 变化。

### 5.2 Trading Health

位置：`backend/trading/health.py`

接口：

```python
def check_trading_schema(db_path: str) -> dict:
    ...
```

返回字段：

```json
{
  "status": "ok",
  "expected_version": 4,
  "applied_version": 4,
  "missing_versions": []
}
```

`GET /api/health` 同时检查：

- 普通数据库 `SELECT 1`。
- 交易迁移版本。

任一失败时 HTTP 状态码返回 503，响应中明确指出失败层。

### 5.3 Provider Factory

位置：`backend/trading/providers/factory.py`

接口：

```python
def build_market_data_provider(settings) -> MarketDataProvider:
    ...
```

行为：

1. 解析 `TRADING_PROVIDER_PRIORITY`，按顺序创建 Provider。
2. 支持 `eastmoney` 和 `akshare`。
3. 未知 Provider 名称在启动时直接报配置错误。
4. 使用 `TRADING_PROVIDER_TIMEOUT_SECONDS` 和 `TRADING_PROVIDER_MAX_RETRIES`。
5. 返回 `CompositeProvider`，业务层不感知具体数据源。

Web 路由与 Scheduler 必须通过同一工厂创建 Provider，禁止继续传 `provider=None`。

### 5.4 Data Job Service

位置：`backend/trading/services/data_job_service.py`

接口：

```python
class DataJobService:
    def run_next_queued_job(self, *, owner_id: str, now: datetime) -> dict | None:
        ...

    def run_job(self, job_id: int, *, owner_id: str, now: datetime) -> dict:
        ...
```

职责：

- 原子领取一个可执行的 QUEUED 任务。
- 把任务转为 RUNNING 并递增 `attempts`。
- 根据 `job_type` 调用 MarketDataService。
- 写入 `progress`、`result_json` 或 `error_json`。
- 成功转为 SUCCEEDED。
- 失败且仍有重试额度时重新进入 QUEUED。
- 第三次失败后转为 FAILED。

支持的手工数据任务：

| job_type | 行为 |
|---|---|
| `update_bars` | 更新指定股票或最新默认股票池的日线 |
| `backfill_bars` | 按开始、结束日期回补指定股票日线 |
| `refresh_calendar` | 刷新给定日期范围的交易日历 |
| `validate_data` | 执行数据质量门禁并保存摘要 |

同一执行器还支持 Scheduler 创建的内部任务：

| job_type | 行为 |
|---|---|
| `update_market_data` | 更新默认股票池与配置基准的目标交易日日线 |
| `generate_daily_plan` | 对所有 active 账户运行门禁并生成或复用计划 |
| `backup_database` | 执行 SQLite 在线备份与保留清理 |

当请求代码列表非空而 Provider 返回 0 条记录时，任务失败，错误码为 `EMPTY_PROVIDER_RESULT`。请求代码列表为空且股票池也为空时，可以成功跳过，但结果必须写明 `skipped_reason=NO_CODES`。

相同 `job_key` 的重复请求按以下规则返回原任务：

- QUEUED、RUNNING 或 SUCCEEDED：返回原任务。
- FAILED 且已达到三次上限：返回原 FAILED 任务，不自动清零尝试次数。
- 改变交易日、代码列表或日期范围会产生不同请求哈希和新任务。

### 5.5 Repository 原子任务 API

位置：`backend/trading/repository.py`

新增接口：

```python
def claim_next_job(
    self,
    *,
    owner_id: str,
    now: datetime,
    max_attempts: int = 3,
) -> dict | None:
    ...

def complete_job(self, job_id: int, *, result: dict) -> None:
    ...

def retry_or_fail_job(
    self,
    job_id: int,
    *,
    error: dict,
    max_attempts: int = 3,
) -> str:
    ...

def requeue_stale_jobs(self, *, stale_before: datetime) -> int:
    ...

def acquire_job_lock(
    self,
    *,
    lock_key: str,
    owner_id: str,
    now: datetime,
    expires_at: datetime,
) -> bool:
    ...

def release_job_lock(self, *, lock_key: str, owner_id: str) -> None:
    ...
```

`claim_next_job` 使用 `BEGIN IMMEDIATE`，在同一事务内选择任务并更新状态，保证两个 Scheduler 不能同时领取同一任务。

锁过期后允许其他实例接管。释放锁时必须同时匹配 `lock_key` 与 `owner_id`，不能删除其他进程持有的锁。

### 5.6 Scheduler Runner

位置：

- `backend/trading/jobs/scheduler.py`
- `backend/trading/jobs/runner.py`

`scheduler.py` 只保留进程生命周期与轮询；`runner.py` 负责可测试的单次调度决策。

接口：

```python
class SchedulerRunner:
    def tick(self, now: datetime | None = None) -> dict:
        ...
```

`tick()` 顺序：

1. 使用 `ZoneInfo(settings.trading_timezone)` 获得上海时区时间。
2. 回收超过配置阈值的 RUNNING 任务。
3. 消费已有 QUEUED 手工任务。
4. 判断目标日期是否为交易日。
5. 为已经到期但尚未成功的定时任务创建幂等 Job。
6. 领取并执行新创建的 Job。
7. 返回本轮创建、领取、成功、重试和失败数量。

`TRADING_SCHEDULE_ENABLED=false` 时，Scheduler 仍消费用户手工创建的 QUEUED 数据任务，但不创建 20:15、20:25 和 23:30 定时任务。部署验收需要在 `.env` 中显式设置 `TRADING_SCHEDULE_ENABLED=true`。

默认定时任务：

| 时间 | job_key 前缀 | 行为 |
|---|---|---|
| 20:15 | `update_market_data` | 更新默认股票池和基准行情 |
| 20:25 | `generate_daily_plan` | 运行数据门禁并生成计划 |
| 23:30 | `backup_database` | 执行 SQLite 在线备份 |

Scheduler 在 20:15:01 或进程晚启动时仍要补建 20:15 任务。执行依据是“计划时间已到且数据库中没有成功的同键 Job”，不再使用内存 `last_run`。

### 5.7 交易日判断

优先使用 Provider 的交易日历。

- Provider 明确返回休市：不创建行情或计划定时任务。
- Provider 明确返回交易日：正常创建。
- Provider 不可用：记录 `CALENDAR_UNAVAILABLE`，不创建新的计划任务。
- 备份任务不依赖交易日，可以每天执行。

禁止在 Provider 不可用时简单把所有工作日视为交易日。

### 5.8 Docker Compose

文件：

- `docker-compose.yml`
- `.dockerignore`
- `.env.example`
- `deploy/README.md`

Web 服务：

```yaml
ports:
  - "${STOCKPULSE_BIND_HOST:-0.0.0.0}:${STOCKPULSE_PORT:-8888}:8888"
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/health')"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 20s
```

Scheduler 等待 Web 健康后启动，但自身仍执行幂等迁移，不能把 Web 启动顺序当作迁移正确性的唯一保障。

`.dockerignore` 必须排除：

```text
.git
.env
.venv
data
uploads
frontend/node_modules
frontend/dist
deploy/certs
deploy/htpasswd
*.db
*.pem
*.key
__pycache__
.pytest_cache
```

## 6. 数据流

### 6.1 手工数据任务

```text
POST /api/trading/data-jobs
  -> 校验请求
  -> 创建或复用 QUEUED Job
  -> HTTP 202
  -> Scheduler tick
  -> 原子领取 RUNNING
  -> Provider Factory / Composite Provider
  -> MarketDataService
  -> Repository 写入日线
  -> SUCCEEDED 或 QUEUED/FAILED
```

### 6.2 定时任务

```text
Scheduler tick
  -> 上海时区
  -> 交易日历
  -> 计算所有已到期任务
  -> 用确定性 job_key 幂等创建
  -> 获取持久锁
  -> 执行任务
  -> 写入结果并释放锁
```

### 6.3 Web 启动

```text
容器/进程启动
  -> FastAPI lifespan
  -> 交易迁移
  -> 迁移版本校验
  -> 成功后开始监听
```

## 7. 状态机与错误语义

```text
QUEUED -> RUNNING -> SUCCEEDED
              |
              +-> QUEUED  attempts < 3
              |
              +-> FAILED  attempts >= 3

```

本轮不新增取消任务 API；数据库中既有 `CANCELLED` 状态保持兼容，但运行时不会主动生成该状态。

核心错误码：

| code | 含义 |
|---|---|
| `TRADING_SCHEMA_NOT_READY` | 交易迁移缺失或损坏 |
| `PROVIDER_CONFIG_INVALID` | Provider 配置包含未知名称 |
| `PROVIDER_UNAVAILABLE` | 所有配置数据源失败 |
| `EMPTY_PROVIDER_RESULT` | 非空请求未返回任何行情 |
| `CALENDAR_UNAVAILABLE` | 无法确认目标日期是否交易日 |
| `JOB_LOCKED` | 同键任务正由其他 Scheduler 执行 |
| `JOB_MAX_ATTEMPTS` | 任务达到三次失败上限 |

旧行情可以保留，但失败任务不能伪报 SUCCEEDED。数据门禁不得使用陈旧行情生成新的开仓计划。

## 8. 测试设计

### 8.1 Web 与迁移

- 空 SQLite 数据库启动 Web 后，4 个交易迁移版本存在。
- 现有数据库重复启动不产生额外迁移。
- 迁移失败时应用启动失败。
- `/api/health` 在交易迁移缺失时返回 503。

### 8.2 Provider Factory

- 配置 `eastmoney,akshare` 保持顺序。
- 未知 Provider 触发 `PROVIDER_CONFIG_INVALID`。
- 超时和重试参数传入 Composite Provider。
- 测试只使用固定 Provider fixture，不访问外网。

### 8.3 Data Job

- HTTP 202 创建 QUEUED 任务。
- Scheduler 消费后进入 RUNNING 再进入 SUCCEEDED。
- 非空代码返回 0 条时记录 `EMPTY_PROVIDER_RESULT`。
- 前两次失败重新排队，第三次进入 FAILED。
- 相同请求复用 job_key。
- 两个 Repository 连接竞争时只有一个能 claim。

### 8.4 Scheduler

- 20:15:01 能补建并执行 20:15 任务。
- 20:25:01 能同时识别 20:15 和 20:25 中尚未成功的任务。
- 同键成功任务不会重复创建。
- 重启后 RUNNING 超时任务重新进入 QUEUED。
- 上海时区与服务器系统时区无关。
- 休市日不创建行情和计划任务。
- Provider 日历失败时不创建计划。

### 8.5 部署

- `docker compose config` 成功。
- `docker compose build` 成功。
- `docker compose up -d` 后 Web 变为 healthy。
- `http://服务器IP:端口/preview` 返回 200。
- `/api/health` 返回 Web、DB 和 trading schema 均为 ok。
- 创建手工数据任务后能够观察到终态。
- 后端全量 Pytest 和前端生产构建通过。

## 9. 验收标准

以下条件必须全部满足：

1. 空库只启动 Web 即可使用首个交易 API。
2. 当前现有库启动后自动补齐交易迁移。
3. Web 和 Scheduler 不再出现 `provider=None`。
4. 非空行情任务 0 行结果不得标记成功。
5. Scheduler 到点后、晚启动和重启三种场景均可正确执行。
6. 两个 Scheduler 竞争时同一 Job 只执行一次。
7. 任务失败最多重试 3 次并可从 API 查看错误。
8. 默认 Compose 可通过 IP + 配置端口访问。
9. 镜像构建上下文不包含 `.env`、数据库、备份和虚拟环境。
10. 后端全量测试、前端生产构建、Compose 构建和运行冒烟全部通过。

## 10. 实施顺序

1. Web lifespan 与交易迁移健康检查。
2. Provider Factory 与路由运行时装配。
3. Repository 原子任务和锁接口。
4. Data Job Service。
5. Scheduler Runner 与交易日判断。
6. Compose、`.dockerignore` 和部署说明。
7. 全量回归与服务器冒烟。
