# Azure Functions Python 3.13 base
FROM mcr.microsoft.com/azure-functions/python:4-python3.13-appservice

# Install Google Chrome
RUN apt-get update && apt-get install -y gnupg ca-certificates wget --no-install-recommends \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor | tee /usr/share/keyrings/google-chrome.gpg > /dev/null \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" | tee /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y google-chrome-stable fonts-liberation --no-install-recommends \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Functions working directory
WORKDIR /home/site/wwwroot

# Copy project
COPY app ./app
COPY requirements.txt ./
COPY azure_fn/ ./

# Install deps
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir -r azure_fn/requirements.txt || true

ENV CHROME_PATH=/usr/bin/google-chrome \
    WEBSITES_PORT=80 \
    FUNCTIONS_WORKER_PROCESS_COUNT=1 \
    PYTHONUNBUFFERED=1

EXPOSE 80

CMD ["/azure-functions-host/Microsoft.Azure.WebJobs.Script.WebHost"]


