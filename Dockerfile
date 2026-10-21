FROM python:3.12-slim
WORKDIR /app
COPY seller_check_mcp.py .
USER nobody
ENTRYPOINT ["python3", "-u", "seller_check_mcp.py"]
