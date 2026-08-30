"""Save products from several processes at once and report what survived.

    python3 tests/race_check.py 4 8        # 4 processes x 8 products

Saving a product is a read-modify-write of one JSON file. Before the fix the
guard was a threading.Lock, which other gunicorn workers cannot see, so two
workers saving at the same time erased each other. This is the regression
check for that bug: it must print "lost 0".
"""
import multiprocessing
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def worker(n, saves, catalog_path, db_path):
    os.environ["CATALOG_PATH"] = catalog_path
    os.environ["DB_PATH"] = db_path
    import catalog
    from db import init_db
    init_db()
    for i in range(saves):
        catalog.upsert({"name": f"W{n}-{i}", "priceNgn": 500 + i,
                        "category": "bags"}, actor="admin")


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    saves = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    # a private folder per run so two runs can never share a catalogue
    folder = tempfile.mkdtemp(prefix="jaura-race-")
    catalog_path = os.path.join(folder, "catalog.json")
    db_path = os.path.join(folder, "jaura.db")

    os.environ["CATALOG_PATH"] = catalog_path
    os.environ["DB_PATH"] = db_path
    import catalog
    from db import init_db
    init_db()

    ps = [multiprocessing.Process(target=worker, args=(n, saves, catalog_path, db_path))
          for n in range(workers)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()

    survived = len(catalog.overrides()["products"])
    expected = workers * saves
    print(f"{expected} saves from {workers} processes -> survived {survived}, "
          f"lost {expected - survived}")
    return 0 if survived == expected else 1


if __name__ == "__main__":
    sys.exit(main())
