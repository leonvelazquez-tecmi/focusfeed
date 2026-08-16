import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import FeedCuratorHTTPHandler
from app.db import init_db

try:
    init_db()
except Exception:
    pass

class handler(FeedCuratorHTTPHandler):
    pass
