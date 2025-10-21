import uuid
import httpx
import asyncio
import json
import re
import time
from curl_cffi.requests.errors import RequestsError
from pathlib import Path
from pydantic import ValidationError
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from environs import Env
from schemas import (
    MessageJsonData,
    CompletionsJsonData,
    TheB_Data,
    HuggingChatData,
    OpenAiData,
    Choices,
    Message,
    Usage,
    Content,
    ChunkJson,
)
from hugging_chat.conversation import HuggingChat_RE
from chatgpt_web.conversation import ChatGPT_Web_RE
from utility import color_print, get_openai_chunk_response, get_openai_chunk_response_end, get_response_headers

env = Env()
env.read_env()

API_HOST = env("API_HOST", "http://localhost:5000")

async_client = httpx.AsyncClient()
# theb_ai = TheB_AI_RE(async_client)
theb_ai = None
hf_chat = HuggingChat_RE(async_client)
chatgpt_web = ChatGPT_Web_RE()
# deepseek_web = Deepseek_Web_RE()
deepseek_web = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await async_client.aclose()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def load_env_middleware(request: Request, call_next):
    env.read_env(override=True)
    response = await call_next(request)
    return response


@app.post("/api/anthropic/v1/messages")
async def anthropic_messages(message_json_data: MessageJsonData, background_tasks: BackgroundTasks):
    messages_str = json.dumps(
        [jsonable_encoder(message, exclude_unset=True) for message in message_json_data.messages], ensure_ascii=False
    )
    response = await theb_ai.conversation(
        message_json_data.model, messages_str, message_json_data.temperature, message_json_data.top_p
    )
    background_tasks.add_task(response.aclose)
    lock = asyncio.Lock()

    async def content_generator():
        async with lock:
            accumulated_response_text = ""
            index = 0
            async for line in response.aiter_lines():
                if line and not line.startswith("event: "):
                    line_content = re.sub("^data: ", "", line)
                    try:
                        data = TheB_Data(**json.loads(line_content))
                    except (json.JSONDecodeError, ValidationError):
                        continue
                    text_delta = data.args.content.replace(accumulated_response_text, "")
                    yield (
                        f"data: {ChunkJson(delta=Content(type='text', text=text_delta), index=index).model_dump_json()}\n\n"
                        if message_json_data.stream
                        else text_delta
                    )
                    index += 1
                    accumulated_response_text = data.args.content
                if line == "event: end":
                    break

    response_headers = get_response_headers(message_json_data.stream)
    return (
        StreamingResponse(content_generator(), headers=response_headers)
        if message_json_data.stream
        # for summarize
        else JSONResponse(
            {"content": [{"text": "".join([text_delta async for text_delta in content_generator()])}]},
            headers=response_headers,
        )
    )


@app.post("/api/openai/v1/chat/completions")
async def openai_chat_completions(
    comletions_json_data: CompletionsJsonData,
    background_tasks: BackgroundTasks,
    authorization: str = Header(env("OPENAI_API_KEY")),
):
    model = comletions_json_data.model
    stream = comletions_json_data.stream
    response_headers = get_response_headers(stream)
    messages_str = json.dumps(
        [jsonable_encoder(message, exclude_unset=True) for message in comletions_json_data.messages], ensure_ascii=False
    )

    supported_prefixes = {"gpt-3.5", "gpt-4", "gpt-5", "o1", "o3", "o4"}
    if any(model.startswith(prefix) for prefix in supported_prefixes):
        if not env.bool("USE_CHATGPT_WEB", True):
            # reverse to openai completions
            openai_api = env("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
            req = async_client.build_request(
                "POST",
                openai_api,
                headers={"Authorization": authorization},
                json=comletions_json_data.model_dump(exclude_unset=True),
                timeout=None,
            )
            resp = await async_client.send(req, stream=True)
            background_tasks.add_task(resp.aclose)
            return (
                StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=response_headers)
                if stream
                else JSONResponse(
                    json.loads(await resp.aread()), status_code=resp.status_code, headers=response_headers
                )
            )
        else:
            response = await chatgpt_web.conversation(model, comletions_json_data.messages)

            # imitate to openai completions response
            async def content_generator():
                openai_data = get_openai_chunk_response(model)
                yield (f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n" if stream else "")

                accumulated_response_text = ""
                try:
                    async for line in response.aiter_lines():
                        if line:
                            line_content = re.sub("^data: ", "", line.decode("utf-8"))
                            if line_content == "[DONE]":
                                yield get_openai_chunk_response_end(model, stream)
                                break

                            try:
                                json_content = json.loads(line_content)
                                message = json_content["message"]
                                message = Message(**message)
                            except (json.JSONDecodeError, KeyError, TypeError, ValidationError):
                                color_print(f"Not supported line: {line_content}", "yellow")
                                continue
                            yield_line = None
                            if message.author.role == "assistant" and message.status == "in_progress":
                                yield_line = message.content.parts[0].replace(accumulated_response_text, "")
                                accumulated_response_text = message.content.parts[0]
                            elif message.author.role == "tool" and message.content.content_type == "multimodal_text":
                                # download image to generated_images folder and return markdown image
                                file_id = message.content.parts[0].asset_pointer.split("://")[-1]
                                image_details = await chatgpt_web.download_image(file_id)
                                file_name = image_details["file_name"]
                                image_file = Path(f"generated_images/{file_name}")
                                image_file.parent.mkdir(
                                    parents=True, exist_ok=True, mode=0o777
                                )  # 0o777 is 755 in octal
                                image_file.write_bytes(image_details["image"])
                                image_url = f"{API_HOST}/image/{file_name}"
                                yield_line = f"![]({image_url})"

                            if yield_line is not None:
                                openai_data.choices = [Choices(delta=Message(content=yield_line))]
                                yield (
                                    f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n"
                                    if stream
                                    else yield_line
                                )

                except RequestsError as e:
                    color_print(str(e), "red")
                    # markdown red color
                    openai_data.choices = [
                        Choices(delta=Message(content="\n```diff\n- Something went wrong, please try again.\n```"))
                    ]
                    yield f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n"

    # imitate to openai completions response
    elif theb_ai and model in theb_ai.model_key_mapping.values():
        response = await theb_ai.conversation(
            model, messages_str, comletions_json_data.temperature, comletions_json_data.top_p
        )
        lock = asyncio.Lock()

        async def content_generator():
            async with lock:
                openai_data = get_openai_chunk_response(model)
                yield (f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n" if stream else "")

                accumulated_response_text = ""
                async for line in response.aiter_lines():
                    if line and not line.startswith("event: "):
                        line_content = re.sub("^data: ", "", line)
                        try:
                            data = TheB_Data(**json.loads(line_content))
                        except (json.JSONDecodeError, ValidationError):
                            continue
                        text_delta = data.args.content.replace(accumulated_response_text, "")
                        openai_data.choices = [Choices(delta=Message(content=text_delta))]
                        yield (f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n" if stream else text_delta)
                        accumulated_response_text = data.args.content
                    if line == "event: end":
                        yield get_openai_chunk_response_end(model, stream)
                        break

    elif model in hf_chat.model_key_mapping:
        response = await hf_chat.request_conversation(messages_str, model)

        if response.status_code != 200:
            return JSONResponse(json.loads(await response.aread()), status_code=response.status_code)

        async def content_generator():
            openai_data = get_openai_chunk_response(model)
            yield (f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n" if stream else "")

            accumulated_response_text = ""
            async for line in response.aiter_lines():
                try:
                    data = HuggingChatData(**json.loads(line))
                except (json.JSONDecodeError, ValidationError):
                    color_print(f"Erorr parsing response: {line}", "red")
                    raise HTTPException(status_code=400, detail="Error parsing response")
                yield_line = None
                match data.type:
                    case "stream" | "reasoning":
                        if data.token:
                            yield_line = data.token.replace(accumulated_response_text, "").replace("\u0000", "")
                            accumulated_response_text = data.token
                    case "file":
                        # download image to generated_images folder and return markdown image
                        image = await hf_chat.generate_image(data.sha)
                        file_name = data.name
                        image_file = Path(f"generated_images/{file_name}")
                        image_file.parent.mkdir(parents=True, exist_ok=True, mode=0o777)  # 0o777 is 755 in octal
                        image_file.write_bytes(await image.aread())
                        image_url = f"{API_HOST}/image/{file_name}"
                        yield_line = f"![{file_name}]({image_url})"
                    case "finalAnswer":
                        yield get_openai_chunk_response_end(model, stream)
                        break
                    case _:
                        color_print(f"Unparsed line: {line}", "yellow")

                if yield_line is not None:
                    openai_data.choices = [Choices(delta=Message(content=yield_line))]
                    yield (f"data: {openai_data.model_dump_json(exclude_unset=True)}\n\n" if stream else yield_line)

            await hf_chat.delete_all_conversation()

    elif deepseek_web and model in deepseek_web.models:
        response = await deepseek_web.completions(messages_str)

        async def content_generator():
            async for line in response.aiter_lines():
                if stream:
                    yield line.decode("utf-8") + "\n"
                else:
                    if line:
                        line_content = re.sub("^data: ", "", line)
                        try:
                            data = OpenAiData(**json.loads(line_content))
                        except (json.JSONDecodeError, ValidationError):
                            continue
                        yield data.choices[0].delta.content

    else:
        raise HTTPException(status_code=400, detail="Model not supported")

    background_tasks.add_task(response.aclose)
    return (
        StreamingResponse(content_generator(), headers=response_headers)
        if stream
        # for summarize
        else JSONResponse(
            OpenAiData(
                choices=[
                    Choices(
                        message=Message(
                            role="assistant",
                            content="".join([text_delta async for text_delta in content_generator()]),
                        ),
                        finish_reason="stop",
                    )
                ],
                created=int(time.time()),
                id=f"chatcmpl-{uuid.uuid4().hex[:29]}",
                object="chat.completion",
                model=model,
                usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            ).model_dump(exclude_unset=True),
            headers=response_headers,
        )
    )


@app.get("/image/{file_name}")
def image(file_name: str):
    return FileResponse(f"generated_images/{file_name}")
