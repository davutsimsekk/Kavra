import threading
import webbrowser

import uvicorn


def main():
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(
        "studio_web.api:app",
        host="127.0.0.1",
        port=8765,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
