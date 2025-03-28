import time
import uuid
import textwrap
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
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

    current_custom_models = data["services"]["chatgpt-next-web"]["environment"]["CUSTOM_MODELS"].split(",")

    def add_company_suffix(model_name: str) -> str:
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
        elif "deepseek" in model_name:
            suffix = "DeepSeek"
        elif "gemma" in model_name:
            suffix = "Gemma"
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

    custom_models_str = ",\\\n".join(current_custom_models)
    # Add proper indentation to the custom models
    indented_custom_models = textwrap.indent(custom_models_str, "        ")[8:]
    data["services"]["chatgpt-next-web"]["environment"]["CUSTOM_MODELS"] = DoubleQuotedScalarString(
        indented_custom_models
    )

    # yaml dump file won't escape \n and \\ properly, write the file and read it again then replace the escaped characters and write it back
    with open("docker-compose.yml", "w") as file:
        yaml.dump(data, file)
    with open("docker-compose.yml", "r") as file:
        file_content = file.read()
    file_content = file_content.replace("\\n", "\n").replace("\\\\", "\\")
    with open("docker-compose.yml", "w") as file:
        file.write(file_content)

    open("scripts/update_signal", "w").close()  # Create a signal file to trigger the update
    color_print("Updated custom models in docker-compose.yml", "green")
