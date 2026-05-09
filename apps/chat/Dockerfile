# syntax=docker/dockerfile:1.7
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN python - <<'PY' > /tmp/requirements.txt
import tomllib

with open("/app/pyproject.toml", "rb") as f:
    project = tomllib.load(f)["project"]

base_dependencies = project["dependencies"]
optional_dependencies = project["optional-dependencies"]
selected_extras = ("runtime-chat-worker",)
resolved_dependencies: list[str] = []


def add_dependency(spec: str) -> None:
    if spec not in resolved_dependencies:
        resolved_dependencies.append(spec)


def resolve_extra(extra_name: str) -> None:
    for spec in optional_dependencies.get(extra_name, []):
        if spec.startswith("banking-agent[") and spec.endswith("]"):
            nested_extras = spec.removeprefix("banking-agent[").removesuffix("]")
            for nested_extra in [part.strip() for part in nested_extras.split(",") if part.strip()]:
                resolve_extra(nested_extra)
            continue
        add_dependency(spec)


for dependency in base_dependencies:
    add_dependency(dependency)

for extra in selected_extras:
    resolve_extra(extra)

print("\n".join(resolved_dependencies))
PY

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps/chat/ /app/apps/chat/
COPY shared/ /app/shared/
COPY config/ /app/config/

ENV PYTHONPATH=/app
CMD ["python", "-m", "apps.chat.src.worker_main"]
