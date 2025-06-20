import re
import json
import os
import asyncio
import nest_asyncio
from random import randint
from curl_cffi.requests import AsyncSession
from seleniumbase import SB
from webscout import tempid
from utility import color_print, get_user_agent

nest_asyncio.apply()  # Fix "RuntimeError: This event loop is already running" error


class TheB_AI_Register:
    api_json_path = "theb_ai/Theb_API.json"
    base_url = "https://beta.theb.ai"
    full_name = "ILoveAI"
    password = "ILoveAI@777"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.headers = {
            "user-agent": get_user_agent("chrome"),
        }

    @classmethod
    def update_file(cls, api_key, organization_id):
        data = []  # Initialize an empty list

        # Check if the JSON file exists and is not empty
        if os.path.exists(cls.api_json_path) and os.path.getsize(cls.api_json_path) > 0:
            # Read the existing JSON data as a list of dictionaries
            with open(cls.api_json_path, "r") as file:
                try:
                    data = json.load(file)
                except json.JSONDecodeError:
                    pass  # Ignore and continue with an empty list if the file is not valid JSON

        # Update the API key and organization ID
        data.append({"API_KEY": api_key, "ORGANIZATION_ID": organization_id})

        # Write the updated data back to the JSON file
        with open(cls.api_json_path, "w") as file:
            json.dump(data, file, indent=4)

    @staticmethod
    async def generate_email() -> str:
        """Generates a temporary email using webscout."""

        try:
            client = tempid.TempMail()
            domains = await client.get_domains()
            email = (await client.create_email(domain=domains[0].name)).email
            color_print(f"Temporary email created: {email}", color="green")
            return email
        finally:
            await client.close()

    def _register_user(self, email: str):
        """Registers on the website using the temporary email."""

        with SB(uc=True, locale_code="en", headed=True, xvfb=self.headless) as sb:
            url = "https://beta.theb.ai/register"
            sb.uc_open_with_reconnect(url, 2)
            sb.uc_gui_click_captcha()
            sb.sleep(randint(2, 3))
            sb.type('input[placeholder="Name"]', self.full_name)
            sb.type('input[placeholder="Email"]', email)
            sb.type('input[placeholder="Password"]', self.password)
            sb.click('button[type="button"]:contains("Create Account")')
            sb.sleep(randint(1, 2))
            if sb.is_text_visible("An error occurred during registration."):
                raise Exception("Failed to register")
            color_print("User registered successfully!", color="green")

    async def _verify_email(self, email):
        """Extracts the verification link from the temporary email and verifies it."""

        color_print("--- VERIFICATION ---", color="red")

        async def get_verification_link() -> str:
            try:
                client = tempid.TempMail()
                color_print("Waiting for the verification email...", color="yellow")
                while True:
                    messages = await client.get_messages(email)
                    if messages:
                        break
                for message in messages:
                    match = re.search(r"https://beta\.theb\.ai/verify-email\?t=[^ ]+", message.body_text)
                    if match:
                        return match.group(0)
            finally:
                await client.close()

        verification_link = await get_verification_link()
        if verification_link:
            color_print("Verification link found in the email.", color="green")
            with SB(uc=True, locale_code="en", headed=True, xvfb=self.headless) as sb:
                sb.open(verification_link)
                sb.sleep(randint(1, 2))
            color_print("Email verified successfully!", color="green")
        else:
            color_print("Verification link not found in the email.", color="red")

    async def _get_api_token(self, email: str):
        """Gets the API token for the user."""

        color_print("--- API TOKEN ---", color="red")
        async with AsyncSession() as session:
            url = "https://beta.theb.ai/api/token"
            payload = {"username": email, "password": self.password}
            headers = self.headers | {"X-Client-Language": "en"}
            response = await session.post(url, data=payload, headers=headers, impersonate="chrome")
            print("Response Status Code (Token):", response.status_code)
            print("Response Content (Token):", response.json())

            if response.status_code == 200:
                access_token = response.json()["access_token"]
                print("Access Token:", access_token)
                return access_token
            else:
                print(
                    "Failed to retrieve API token. Error:",
                    response.json().get("data", {}).get("detail", "Unknown error"),
                )
                return

    async def _get_organization_id(self, access_token):
        color_print("--- ORGANIZATION ID ---", color="red")
        async with AsyncSession() as session:
            url = "https://beta.theb.ai/api/me"
            headers = self.headers | {"Authorization": f"Bearer {access_token}", "X-Client-Language": "en"}
            response = await session.get(url, headers=headers, impersonate="chrome")
            print("Response Status Code (Organization):", response.status_code)
            print("Response Content (Organization):", response.json())
            if response.status_code == 200:
                print("User logged in successfully!")
                organization_id = response.json()["data"]["organizations"][0]["id"]
                print("Organization ID:", organization_id)
                return organization_id
            else:
                print("Failed to Login. Error:", response.json())

    async def generate_api_token(self):
        email = await self.generate_email()
        self._register_user(email)
        await self._verify_email(email)
        api_token = await self._get_api_token(email)
        organization_id = await self._get_organization_id(api_token)
        if api_token is not None and organization_id is not None:
            print(
                f"Successfully Initialized.....\nAPI KEY : \033[92m{api_token}\033[0m\nORGANIZATION ID : \033[92m{organization_id}\033[0m"
            )
            self.update_file(api_key=api_token, organization_id=organization_id)
        else:
            color_print("Failed to initialize. Please try again.", color="red")


async def async_generate_api_token(at_once: int = 3):
    """Generates multiple API tokens concurrently using asyncio."""

    try:
        # Create list of coroutines to run concurrently
        tasks = [TheB_AI_Register().generate_api_token() for _ in range(at_once)]

        # Run all tasks concurrently and wait for completion
        await asyncio.gather(*tasks)

        color_print("All API token generation tasks completed successfully.", "green")
    except Exception as e:
        color_print(f"Error during API token generation: {str(e)}", "red")
