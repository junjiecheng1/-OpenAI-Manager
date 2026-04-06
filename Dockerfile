FROM python:3.12-slim

# 系統依賴: Chromium + Xvfb 虛擬顯示 + noVNC 遠程查看
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium 依賴
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libatspi2.0-0 libwayland-client0 \
    # 虛擬顯示 (headless=False 必需)
    xvfb x11vnc \
    # noVNC (瀏覽器遠程查看)
    novnc websockify \
    # 字體
    fonts-noto-cjk fonts-liberation \
    # 工具
    curl ca-certificates supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依賴安裝
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . && \
    playwright install chromium && \
    playwright install-deps chromium

# 複製代碼
COPY . .

# 創建數據目錄
RUN mkdir -p tokens

# Supervisor 配置 — 管理 Xvfb + VNC + App
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# 端口: 8900=App, 6080=noVNC(瀏覽器查看)
EXPOSE 8900 6080

# 環境變量
ENV PYTHONUNBUFFERED=1 \
    TOKEN_OUTPUT_DIR=/app/tokens \
    DISPLAY=:99 \
    VNC_PASSWORD=openai123

# 啟動入口
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
