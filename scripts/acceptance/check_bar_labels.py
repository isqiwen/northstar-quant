"""Audit two already retained Tushare responses; never download or publish data."""

import argparse
import json
from pathlib import Path

from northstar_quant import code_revision
from northstar_quant.data_management.tushare.resolutions import compare_resolutions


def main() -> None:
    parser = argparse.ArgumentParser(description="只读核对已留存的 Tushare 分钟标签与 OHLCV")
    parser.add_argument("--one-minute", type=Path, required=True)
    parser.add_argument("--coarse", type=Path, required=True)
    parser.add_argument("--minutes", type=int, choices=(5, 15, 30, 60), required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    for path in (args.one_minute, args.coarse):
        if path.stat().st_size > 16 * 1024 * 1024:
            parser.error("单个已留存响应不得超过 16 MiB")
    result = compare_resolutions(
        args.one_minute.read_bytes(),
        args.coarse.read_bytes(),
        contract=args.contract,
        minutes=args.minutes,
        start=args.start,
        end=args.end,
    )
    result["implementation"] = code_revision()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
