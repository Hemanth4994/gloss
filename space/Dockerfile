# Hugging Face Space (Docker SDK): the Gloss demo, one container serving the web page and the MCP endpoint.
# The agent code is installed from the git tag frozen-v1, so the demo runs exactly the code the test split was run on.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH" PORT=7860 PYTHONUNBUFFERED=1 GRADIO_ANALYTICS_ENABLED=False
WORKDIR /home/user/app

# Versions the tests and the evaluation ran with.
RUN pip install --no-cache-dir "gradio==6.31.0" "mcp==2.3.0" "langgraph==1.2.19" "langchain-google-genai==4.4.2" \
    "gloss[agent,app] @ git+https://github.com/Hemanth4994/gloss@frozen-v1"

COPY --chown=user app.py results_summary.json ./
# The warehouse is synthetic and built from a fixed seed, so it is identical to the one the evaluation used.
RUN python -m gloss.pipeline --out data/gloss.duckdb > /dev/null

EXPOSE 7860
CMD ["python", "app.py"]
