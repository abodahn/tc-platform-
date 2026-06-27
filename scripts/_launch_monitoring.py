"""TC Platform launch helper - starts the Server Monitoring central server on
port 5002. Lives in tc-platform (does NOT modify the existing app)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.abspath(os.path.join(HERE, "..", ".."))
APP_DIR = os.path.join(WORKSPACE, "windows-monitoring-command-center",
                       "windows-monitoring-command-center", "central_server")

os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from app import app, socketio  # noqa: E402

print("Starting Server Monitoring on http://127.0.0.1:5002 ...")
socketio.run(app, host="127.0.0.1", port=5002, debug=False,
             allow_unsafe_werkzeug=True)
