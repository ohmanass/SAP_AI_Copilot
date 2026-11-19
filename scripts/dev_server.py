"""
Local server (interface and API, see app/web.py), restarted automatically on
every change of app/*.py. Started by start.ps1 / start.cmd:

    python scripts/dev_server.py          port 8000
    python scripts/dev_server.py 8001

Why not "uvicorn --reload": on Windows it stops the old server with a Ctrl+C
event, then waits for it without any time limit. When the event is lost (no
console, many watched files on OneDrive...), the reload hangs on "Reloading..."
and the old code keeps running. watchfiles stops the old server with a time
limit (then kills it) and always starts the new one.

Only app/ is watched: a change of .env or of the dependencies still needs a
restart of this script (Ctrl+C, then run it again).
"""

import sys
from pathlib import Path

import watchfiles

ROOT = Path(__file__).resolve().parent.parent


def serve(port: int):
    """Runs in a new process at every restart."""
    import uvicorn

    sys.path.insert(0, str(ROOT))
    uvicorn.run("app.api:app", host="127.0.0.1", port=port)


def announce(changes):
    files = sorted({Path(path).relative_to(ROOT).as_posix() for _, path in changes})
    # ASCII only: the Windows console may not encode other characters
    print(f"\n>> Restarting: {', '.join(files[:5])}{' ...' if len(files) > 5 else ''}\n", flush=True)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"SAP AI Copilot on http://127.0.0.1:{port}/ - restarted automatically when app/ changes, "
          "F5 after a change of the interface (Ctrl+C to stop)", flush=True)
    watchfiles.run_process(
        ROOT / "app",
        target=serve,
        args=(port,),
        watch_filter=watchfiles.PythonFilter(),
        callback=announce,
    )


if __name__ == "__main__":
    main()
