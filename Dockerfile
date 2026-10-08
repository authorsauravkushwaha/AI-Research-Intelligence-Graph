# NEXUS API + front end. The native C++ kernel is compiled during the build,
# so the container needs no toolchain at runtime.
FROM python:3.12-slim AS native
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY native/ ./native/
RUN cd native && make -j"$(nproc)"

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY data/ data/
COPY run.py ./
COPY --from=native /src/native/build/bin/nexus-kernel native/build/bin/nexus-kernel
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
