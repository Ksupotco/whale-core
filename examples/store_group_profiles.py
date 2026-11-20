"""Replay an imported QQ group into Whale memory. Default mode runs on a SQLite copy."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from examples.build_group_portraits import load_group_inputs
from examples.import_qce_group_memory import _backup
from whale_core.memory.group_replay import replay
from whale_core.memory.sqlite_store import SqliteMemoryStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir",required=True,type=Path)
    parser.add_argument("--group-id",required=True)
    parser.add_argument("--owner-qq",required=True)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("files",nargs="+",type=Path)
    args = parser.parse_args()
    inputs = load_group_inputs(args.config_dir/"group_archive.sqlite3",args.group_id,args.files)
    database = args.config_dir/"memory.sqlite3"
    if not database.is_file():
        raise FileNotFoundError(database)
    with TemporaryDirectory() as scratch:
        target = database
        if args.apply:
            print("backup",_backup(database))
        else:
            target = Path(scratch)/"memory.sqlite3"
            with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as src, closing(sqlite3.connect(target)) as dest:
                src.backup(dest)
        with closing(SqliteMemoryStore(target)) as store:
            print(json.dumps(replay(store,args.group_id,args.owner_qq,inputs),sort_keys=True))


if __name__=="__main__":
    main()
