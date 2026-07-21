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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
