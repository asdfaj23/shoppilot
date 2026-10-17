# Week4 · 一键 Docker Demo（面试可现场演示）
# 构建：docker build -f week4/Dockerfile -t ecommerce-agent .
# 运行：docker run -p 8501:8501 -e DEEPSEEK_API_KEY=你的key ecommerce-agent
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py .
COPY data ./data
COPY week1 ./week1
COPY week2 ./week2
COPY week3 ./week3
COPY week4 ./week4

EXPOSE 8501
CMD ["streamlit", "run", "week4/app.py", "--server.address", "0.0.0.0"]
