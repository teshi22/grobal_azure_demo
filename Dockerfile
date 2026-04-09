FROM python:3.12-slim

WORKDIR /app

COPY . user_agent/
WORKDIR /app/user_agent

# azure-ai-agentserver-agentframework (beta) は agent-framework-core<=1.0.0rc3 を要求し、
# agent-framework (GA) は agent-framework-core==1.0.0 を要求するため競合する。
# 解決: agentserver を先にインストールして core を固定 → 残りを通常インストール →
# agent-framework GA はメタパッケージとして --no-deps で追加
RUN pip install --no-cache-dir azure-ai-agentserver-agentframework==1.0.0b17 && \
    grep -v "^agent-framework" requirements.txt | pip install --no-cache-dir -r /dev/stdin && \
    pip install --no-cache-dir --no-deps agent-framework==1.0.0

EXPOSE 8088

CMD ["python", "src/hosted.py"]
