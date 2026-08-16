import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.server import FeedCuratorHTTPHandler, init_db
init_db()
FeedCuratorHTTPHandler.seed_sample_dataset(FeedCuratorHTTPHandler)

class handler(FeedCuratorHTTPHandler):
    pass
