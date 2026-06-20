from pathlib import Path

import pandas as pd


def detect_encoding(csv_path):
    csv_path = Path(csv_path)
    raw = csv_path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def read_csv_frame(csv_path):
    csv_path = Path(csv_path)
    return pd.read_csv(
        csv_path,
        sep=None,
        engine="python",
        dtype=str,
        keep_default_na=False,
        encoding=detect_encoding(csv_path),
    )


def read_csv_rows(csv_path):
    return read_csv_frame(csv_path).to_dict("records")
