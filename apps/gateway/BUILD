# Gateway Service BUILD file

python_sources(
    name="gateway",
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
    args=["apps.gateway.main:app", "--host=0.0.0.0", "--port=8000", "--reload"],
    dependencies=[":gateway"],
)

