import time
import uuid
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString
from typing import Literal
from fake_useragent import UserAgent
from schemas import Choices, Message, OpenAiData


def color_print(text: str, color: Literal["red", "green", "yellow", "blue"]):
    colors = {"red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m", "blue": "\033[94m"}
    print(f"{colors[color]}{text}\033[0m")


def get_user_agent(browser: str = None) -> str:
    ua = UserAgent()
    try:
        return ua.random if not browser else getattr(ua, browser)
    except Exception:
        return ua.random


def get_response_headers(stream: bool):
    return {
        "Transfer-Encoding": "chunked",
        "X-Accel-Buffering": "no",
        "Content-Type": ("text/event-stream;" if stream else "application/json;" + " charset=utf-8"),
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }


def get_openai_chunk_response(model: str) -> OpenAiData:
    return OpenAiData(
        choices=[Choices(delta=Message(role="assistant", content=""))],
        created=int(time.time()),
        id=f"chatcmpl-{uuid.uuid4().hex[:29]}",
        object="chat.completion.chunk",
        model=model,
    )


def get_openai_chunk_response_end(model: str, stream: bool) -> str:
    openai_data = get_openai_chunk_response(model)
    openai_data.choices = [Choices(delta=Message(content=""), finish_reason="stop")]
    return f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\ndata: [DONE]\n\n" if stream else ""


def update_nextchat_custom_models(models: list[str], delete_models: list[str] = []):
    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    yaml.width = 4096

    with open("./docker-compose.yml", "r") as file:
        data = yaml.load(file)

    raw_models = data["services"]["chatgpt-next-web"]["environment"]["CUSTOM_MODELS"]
    current_custom_models = [m.strip() for m in str(raw_models).split(",") if m.strip()]

    def add_company_suffix(model_name: str) -> str:
        suffix = ""
        if "llama" in model_name:
            suffix = "Meta"
        elif "gpt" in model_name or "o1" in model_name:
            suffix = "OpenAI"
        elif "gemini" in model_name:
            suffix = "Google"
        elif "claude" in model_name:
            suffix = "Anthropic"
        elif "command-r-plus" in model_name:
            suffix = "CohereForAI"
        elif "qwen" in model_name or "qwq" in model_name:
            suffix = "Qwen"
        elif "hermes" in model_name:
            suffix = "NousResearch"
        elif "mixtral" in model_name or "mistral" in model_name:
            suffix = "MistralAI"
        elif "phi" in model_name or "wizardlm" in model_name:
            suffix = "Microsoft"
        elif "deepseek" in model_name:
            suffix = "DeepSeek"
        elif "gemma" in model_name:
            suffix = "Gemma"
        elif "glm" in model_name:
            suffix = "ZhipuAI"
        elif "kat" in model_name:
            suffix = "Kwaipilot"
        elif "kimi" in model_name:
            suffix = "KimiAI"
        elif "ernie" in model_name:
            suffix = "Baidu"
        elif "apertus" in model_name:
            suffix = "Apertus"
        elif "smol" in model_name:
            suffix = "HuggingFaceTB"
        elif "aya" in model_name or "command" in model_name:
            suffix = "CohereLabs"
        elif "baichuan" in model_name:
            suffix = "BaichuanAI"
        elif "afm" in model_name:
            suffix = "ArceeAI"
        elif "marin" in model_name:
            suffix = "Marin"
        elif "lunaris" in model_name or "euryale" in model_name or "stheno" in model_name:
            suffix = "Sao10K"
        elif "arch-router" in model_name:
            suffix = "Katanemo"
        elif "minimax" in model_name:
            suffix = "MinimaxAI"
        return f"{model_name}@{suffix}" if suffix else model_name

    # Add new models
    for model in models:
        model = add_company_suffix(model)
        if model not in current_custom_models:
            current_custom_models.append(model)
    # Delete models
    for model in delete_models:
        model = add_company_suffix(model)
        if model in current_custom_models:
            current_custom_models.remove(model)

    custom_models_str = ",\n".join(current_custom_models)
    data["services"]["chatgpt-next-web"]["environment"]["CUSTOM_MODELS"] = LiteralScalarString(custom_models_str)

    with open("docker-compose.yml", "w") as file:
        yaml.dump(data, file)

    open("scripts/update_signal", "w").close()  # Create a signal file to trigger the update
    color_print("Updated custom models in docker-compose.yml", "green")
