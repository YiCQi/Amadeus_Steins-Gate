"""Start the Amadeus server using host/port from config.toml."""
import webbrowser

import uvicorn

from server.config import load_config

if __name__ == "__main__":
    srv = load_config().get("server", {})
    host, port = srv.get("host", "127.0.0.1"), int(srv.get("port", 8710))
    webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run("server.main:app", host=host, port=port)
