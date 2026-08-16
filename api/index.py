import sys
import os

# Add parent directory to path so app modules can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import FeedCuratorHTTPHandler, init_db

# Initialize database on cold start
init_db()
FeedCuratorHTTPHandler.seed_sample_dataset(FeedCuratorHTTPHandler)

class handler(FeedCuratorHTTPHandler):
    pass
