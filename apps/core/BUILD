# Core Service BUILD file

python_sources(
    name="core",
    sources=["**/*.py"],
    dependencies=[
        "//:reqs",
        "//shared:lib",
    ],
)

# Entry point for running the service
pex_binary(
    name="bin",
    entry_point="uvicorn",
    args=["apps.core.src.main:app", "--host=0.0.0.0", "--port=8001", "--reload"],
    dependencies=[":core"],
)

