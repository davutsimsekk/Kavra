import threading
import webbrowser

import uvicorn

APP_HOST = "127.0.0.1"
APP_PORT = 8768


def main():
    threading.Timer(1.2, lambda: webbrowser.open(f"http://{APP_HOST}:{APP_PORT}")).start()
    uvicorn.run(
        "studio_web.api:app",
        host=APP_HOST,
        port=APP_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
