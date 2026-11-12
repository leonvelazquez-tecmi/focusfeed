"""reclassify_podcasts cambia solo content_type y solo donde el feed dice podcast."""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class ReclassifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_KEY", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        import app.db as db
        self.db = db
        db.DB_PATH = os.path.join(self.tmp, "t.db")
        db.init_db()
        conn = db.get_db_connection()
        conn.execute("INSERT INTO feeds (id, title, feed_url, feed_type) VALUES (1,'P','http://x','rss')")
        for gid, tipo in (("a", "article"), ("b", "article"), ("c", "podcast")):
            conn.execute("INSERT INTO items (feed_id, guid, title, url, content_type) VALUES (1,?,?,?,?)",
                         (gid, gid, gid, tipo))
        conn.commit()
        conn.close()
        spec = importlib.util.spec_from_file_location("rp", os.path.join(ROOT, "routines", "reclassify_podcasts.py"))
        self.rp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rp)

    def test_solo_mal_tipados_del_feed(self):
        ids = self.rp.mal_tipados(1, ["a", "c", "zzz"])
        self.assertEqual(len(ids), 1)

    def test_set_type_y_revertir(self):
        ids = self.rp.mal_tipados(1, ["a", "b"])
        self.rp.set_type(ids, "podcast")
        conn = self.db.get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM items WHERE content_type='podcast'").fetchone()[0], 3)
        conn.close()
        self.rp.set_type(ids, "article")
        self.assertEqual(self.rp.mal_tipados(1, ["a", "b"]), ids)


if __name__ == "__main__":
    unittest.main()
