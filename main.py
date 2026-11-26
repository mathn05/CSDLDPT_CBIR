"""Launch the flower retrieval app, query an image, or rebuild its gallery."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Truy vấn ảnh hoa theo nội dung")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--app", action="store_true", help="Mở giao diện truy vấn")
    mode.add_argument("--index", action="store_true", help="Lập chỉ mục và đồng bộ PostgreSQL")
    mode.add_argument("--search", type=Path, metavar="ẢNH", help="Truy vấn một ảnh từ dòng lệnh")
    args = parser.parse_args()

    if args.index:
        from flower_cbir.services.database import FlowerDatabase
        from flower_cbir.services.index_dataset import build_or_load_index

        data = build_or_load_index()
        database = FlowerDatabase()
        try:
            print(database.publish_index(data))
        finally:
            database.close()
    elif args.search:
        from flower_cbir.services.search import FlowerSearchEngine

        if not args.search.is_file():
            parser.error(f"Không tìm thấy ảnh {args.search}")
        result = FlowerSearchEngine().search(args.search)
        print("Mask:", result["query_info"]["segmentation"]["method"])
        for item in result["results"]:
            print(f"{item['rank']}. {item['filename']}  D={item['distance']:.5f}  S={item['similarity']:.2%}")
    else:
        app = Path(__file__).resolve().parent / "flower_cbir" / "app.py"
        raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)]))


if __name__ == "__main__":
    main()
