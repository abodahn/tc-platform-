"""TC Platform launch helper - starts the Asset & Inventory app on port 5001.
Lives in tc-platform (does NOT modify the existing app). Resolves paths
relative to this file so it is portable."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.abspath(os.path.join(HERE, "..", ".."))
APP_DIR = os.path.join(WORKSPACE, "enterprise_asset_inventory")

os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from app import create_app  # noqa: E402

print("Starting Asset & Inventory on http://127.0.0.1:5001 ...")
create_app().run(host="127.0.0.1", port=5001, debug=False)
