"""Launch the selected application entrypoint with an explicit local listening port."""


def serve(role: str, port: int) -> None:
    import uvicorn

    if not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    module = {
        "live": "apps.live.kernel",
        "live-web": "apps.live",
        "data-hub": "apps.data_hub",
        "research-web": "apps.research",
    }[role]
    uvicorn.run(f"northstar_quant.{module}:application", factory=True, host="127.0.0.1", port=port)
