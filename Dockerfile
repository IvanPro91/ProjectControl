# syntax=docker/dockerfile:1
#
# ProjectControl в Docker.
# ВНИМАНИЕ: панель запускает ТВОИ локальные проекты. Поэтому внутри контейнера
# нужен доступ к диску с проектами, а адрес панели должен быть доступен с хоста.
#
#   docker build -t projectcontrol .
#   docker run -d --name projectcontrol -p 8770:8770 \
#     -v /path/to/your/projects:/projects \
#     -v projectcontrol-data:/data \
#     projectcontrol
#
# Затем открой http://127.0.0.1:8770/ и в настройках укажи корневые папки
# вида /projects/<имя-проекта>.

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PROJECTCONTROL_HOME=/data

WORKDIR /app

# Зависимости ставим отдельным слоем, чтобы кэш не пересобирался на каждый код-фикс
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY projectcontrol ./projectcontrol
COPY run.py pyproject.toml README.md ./

# Том с настройками и базой проектов
VOLUME ["/data"]
EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["python", "run.py"]
CMD ["--host", "0.0.0.0", "--port", "8770", "--no-browser"]
