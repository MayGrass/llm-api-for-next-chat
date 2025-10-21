FROM python:3.11-slim

# install chrome and xvfb
# RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
#     && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
#     && apt-get update \
#     && apt-get install google-chrome-stable xvfb -y \
#     && apt-get clean \
#     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install -U pip && pip install --no-cache-dir -r requirements.txt

EXPOSE 5000

CMD ["uvicorn", "main:app", "--loop" ,"asyncio", "--host", "0.0.0.0", "--port", "5000", "--timeout-keep-alive", "300"]