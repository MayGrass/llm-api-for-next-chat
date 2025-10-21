import json
import random
import string
import httpx
import os
import yaml
from fastapi import HTTPException
from urllib3 import encode_multipart_formdata
from urllib3.fields import RequestField
from utility import color_print, get_user_agent, update_nextchat_custom_models


class HuggingChat_RE:
    hugging_face_url = "https://huggingface.co"
    chat_conversation_url = f"{hugging_face_url}/chat/conversation"
    web_search = False

    def __init__(self, async_client: httpx.AsyncClient = None) -> None:
        self.headers = {
            "Cookie": f"hf-chat={self.hf_chat};token={self.hf_chat_token}",
            "User-Agent": get_user_agent(),
            "Origin": self.hugging_face_url,
        }
        self.async_client = async_client or httpx.AsyncClient()
        self.conversation_id = None
        self.message_id = None
        self.model_key_mapping = self._get_latest_models()

    @property
    def hf_chat(self) -> str:
        return os.environ.get("HUGGING_CHAT")

    @property
    def hf_chat_token(self) -> str:
        return os.environ.get("HUGGING_CHAT_TOKEN")

    @staticmethod
    def generate_random_boundary() -> str:
        return f"----WebKitFormBoundary{''.join(random.sample(string.ascii_letters + string.digits, 16))}"

    @property
    def config(self) -> list:
        config_id_mapping = {
            "Image Generation": "000000000000000000000001",
            "Document parser": "000000000000000000000002",
            "Image editor": "000000000000000000000003",
            "Calculator": "00000000000000000000000c",
            "Fetch URL": "00000000000000000000000b",
            "Web Search": "00000000000000000000000a",
        }

        config = []
        with open("hugging_chat/config.json", "r") as config_file:
            config_json = json.load(config_file)
            self.web_search = config_json["Web Search"]
            for tool_name, is_enabled in config_json.items():
                if is_enabled:
                    config.append(config_id_mapping[tool_name])
        return config

    def _get_latest_models(self) -> dict:
        url = "https://raw.githubusercontent.com/huggingface/chat-ui/refs/heads/main/chart/env/prod.yaml"
        response = httpx.get(url)
        response.raise_for_status()
        yaml_data = yaml.safe_load(response.text)
        models_string = yaml_data["envVars"]["MODELS"]
        models_list = yaml.safe_load(models_string)
        model_key_mapping = {}
        for model in models_list:
            if model.get("description"):
                model_name = model["id"]
                key = model_name.split("/")[-1].lower()
                model_key_mapping[key] = model_name

        existing_models_file = "hugging_chat/models.json"
        with open(existing_models_file, "r") as file:
            existing_models = json.load(file)

        models_to_remove = set()
        if existing_models != model_key_mapping:
            models_to_remove = set(existing_models.keys()) - set(model_key_mapping.keys())
            with open(existing_models_file, "w") as file:
                json.dump(model_key_mapping, file, indent=4)

        update_nextchat_custom_models(model_key_mapping.keys(), models_to_remove)
        return model_key_mapping

    async def _init_conversation(self, model: str, system_prompt: str) -> None:
        max_retries = 3
        retries = 0

        while retries <= max_retries:
            try:
                self.conversation_id = await self._find_conversation_id(model, system_prompt)
                self.message_id = await self._find_message_id()
                break
            except httpx.ReadTimeout:
                color_print("ReadTimeout Error: Retrying...", "yellow")
                retries += 1
        if retries == max_retries:
            color_print("Max retries exceeded. Unable to initialize conversation.", "red")
            raise HTTPException(status_code=500, detail="Unable to initialize conversation.")

    async def _find_conversation_id(self, model: str, system_prompt: str) -> str:
        payload = {"model": model, "preprompt": system_prompt}
        response = await self.async_client.post(self.chat_conversation_url, json=payload, headers=self.headers)
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid Hugging Face chat token.")
        response.raise_for_status()
        response_json = response.json()
        color_print(f"Initialised Conversation ID: {response_json['conversationId']}", "green")
        return response_json["conversationId"]

    async def _find_message_id(self) -> str:
        url = f"{self.hugging_face_url}/chat/api/v2/conversations/{self.conversation_id}"
        response = await self.async_client.get(url, headers=self.headers)
        response.raise_for_status()
        message_id = response.json()["json"]["messages"][0]["id"]
        color_print(f"Initialised Message ID: {message_id}", "green")
        return message_id

    async def delete_all_conversation(self) -> None:
        delete_url = f"{self.hugging_face_url}/chat/api/conversations"
        headers = self.headers | {"Content-Type": f"multipart/form-data; boundary={self.generate_random_boundary()}"}
        response = await self.async_client.delete(delete_url, headers=headers)
        response.raise_for_status()
        color_print("All conversation deleted.", "green")

    async def generate_image(self, sha: str):
        if not self.conversation_id:
            await self._init_conversation()
        url = f"{self.chat_conversation_url}/{self.conversation_id}/output/{sha}"
        response = await self.async_client.get(url, headers=self.headers)
        response.raise_for_status()
        return response

    async def request_conversation(
        self,
        query: str,
        model: str,
        system_prompt: str = "Act as an AI assistant that responds to user inputs in the language they use. Parse the provided JSON-formatted conversation history, but respond only to the final user message without referencing the JSON format. Maintain consistency with previous responses and adapt to the user's language preference.",
    ):
        if not self.hf_chat:
            raise HTTPException(status_code=400, detail="Please set the HUGGING_CHAT_TOKEN environment variable.")

        await self._init_conversation(
            model=self.model_key_mapping.get(model),
            system_prompt=system_prompt,
        )

        url = f"{self.chat_conversation_url}/{self.conversation_id}"

        request_fields = [
            RequestField(
                name="data",
                data=json.dumps(
                    {
                        "inputs": query,
                        "id": self.message_id,
                        "is_retry": False,
                    },
                    ensure_ascii=False,
                ),
                headers={"Content-Disposition": 'form-data; name="data"'},
            ),
        ]
        content, content_type = encode_multipart_formdata(request_fields, boundary=self.generate_random_boundary())
        headers = self.headers | {"Content-Type": content_type}
        req = self.async_client.build_request("POST", url, content=content, headers=headers, timeout=None)
        response = await self.async_client.send(req, stream=True)
        color_print(f"Hugging Chat Response Status Code: {response.status_code}", "blue")
        return response
