from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    db_path: str = str(Path(__file__).parent.parent / "data" / "stock.db")
    upload_dir: str = str(Path(__file__).parent.parent / "uploads")

    # 百度 OCR
    baidu_ocr_api_key: str = ""
    baidu_ocr_secret_key: str = ""

    # LLM 配置（OpenAI 兼容 API，支持 Hermes / DeepSeek / Ollama 等）
    llm_api_base: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = "Hermes"
    llm_max_tokens: int = 0
    llm_temperature: float = 0.7

    # 交易模块配置（文档 13.3）
    trading_enabled: bool = True
    trading_timezone: str = "Asia/Shanghai"
    trading_schedule_enabled: bool = False  # Phase 1 不启用定时
    trading_provider_priority: str = "eastmoney,akshare"
    trading_provider_timeout_seconds: float = 20.0
    trading_provider_max_retries: int = 3
    trading_data_max_missing_ratio: float = 0.05
    trading_benchmark_codes: str = "000300.SH,000905.SH"
    trading_backup_retention_days: int = 30

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
