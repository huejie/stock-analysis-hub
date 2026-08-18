from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _service_block(compose: str, service: str) -> str:
    lines = compose.splitlines()
    marker = f"  {service}:"
    start = lines.index(marker)
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ")
            and not lines[index].startswith("    ")
            and lines[index].rstrip().endswith(":")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_compose_exposes_web_and_waits_for_verified_health():
    compose = _read("docker-compose.yml")

    assert "web:" in compose
    assert "scheduler:" in compose
    assert (
        "${STOCKPULSE_BIND_HOST:-0.0.0.0}:"
        "${STOCKPULSE_PORT:-8888}:8888"
    ) in compose
    assert "http://127.0.0.1:8888/api/health" in compose
    assert "condition: service_healthy" in compose
    assert "deploy/docker-compose.public.yml" in compose
    assert "# nginx:" not in compose


def test_compose_web_and_scheduler_share_persistent_database_path():
    compose = _read("docker-compose.yml")

    assert compose.count("./data:/app/data") == 2
    assert compose.count("DB_PATH: /app/data/stock.db") == 2
    assert "python -m backend.trading.jobs.scheduler" in compose


def test_public_compose_exposes_only_authenticated_nginx():
    public_compose = _read("deploy/docker-compose.public.yml")
    web = _service_block(public_compose, "web")
    scheduler = _service_block(public_compose, "scheduler")
    nginx = _service_block(public_compose, "nginx")

    assert "ports:" not in web
    assert "ports:" not in scheduler
    assert public_compose.count("\n    ports:") == 1
    assert '"80:80"' in nginx
    assert '"443:443"' in nginx
    assert "web:" in nginx
    assert "condition: service_healthy" in nginx
    assert "./nginx.conf:/etc/nginx/conf.d/default.conf:ro" in nginx
    nginx_config = _read("deploy/nginx.conf")
    assert "proxy_pass http://web:8888" in nginx_config
    assert "docker compose -f deploy/docker-compose.public.yml" in nginx_config
    assert "不得与默认 Compose 叠加" in nginx_config


def test_dockerignore_excludes_runtime_data_secrets_and_worktrees():
    entries = {
        line.strip()
        for line in _read(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    required = {
        ".git",
        ".env*",
        "**/.env*",
        "!.env.example",
        "!**/.env.example",
        ".venv",
        ".worktrees",
        "data",
        "backups",
        "uploads",
        "frontend/node_modules",
        "frontend/dist",
        "deploy/certs",
        "deploy/htpasswd",
        "*.db",
        "*.db-shm",
        "*.db-wal",
        "*.pem",
        "*.key",
        "__pycache__",
        ".pytest_cache",
    }
    assert required <= entries


def test_env_example_documents_runtime_settings_without_real_secrets():
    env_example = _read(".env.example")

    assert "STOCKPULSE_BIND_HOST=0.0.0.0" in env_example
    assert "STOCKPULSE_PORT=8888" in env_example
    assert "TRADING_JOB_LOCK_TTL_SECONDS=900" in env_example
    assert "TRADING_STALE_JOB_SECONDS=1800" in env_example
    assert "TRADING_SCHEDULER_POLL_SECONDS=30" in env_example
    assert "BAIDU_OCR_API_KEY=你的API_KEY" in env_example
    assert "BAIDU_OCR_SECRET_KEY=你的SECRET_KEY" in env_example


def test_deployment_guide_separates_local_checks_from_server_deployment():
    guide = _read("deploy/README.md")

    assert "本地验证" in guide
    assert "实际服务器部署" in guide
    assert "未执行" in guide
    assert "服务器IP" in guide
    assert "docker compose config" in guide
    assert "迁移" in guide
    assert "/api/health" in guide
    assert "docker compose logs" in guide
    assert "备份" in guide
    assert "恢复" in guide
    assert "禁止" in guide and "公网" in guide
    assert "deploy/docker-compose.public.yml" in guide
    assert "run_backup" in guide
    assert "integrity_ok" in guide
    assert "sha256sum" in guide
    assert "把整个 `data/` 复制" not in guide
    assert "备份 `data/`" not in guide

    backup_source = _read("backend/trading/jobs/backup_database.py")
    assert "src.backup(dst)" in backup_source
    assert "PRAGMA integrity_check" in backup_source
    assert "hashlib.sha256" in backup_source
