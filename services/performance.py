from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_baseline(csv_path: str) -> dict:
    df = pd.read_csv(csv_path)
    if df.shape[1] < 3:
        raise ValueError("CSV must contain at least Timestamp, IOPS and Bytes/s columns.")
    timestamps = pd.to_datetime(df.iloc[:, 0])
    iops = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    throughput = pd.to_numeric(df.iloc[:, 2], errors="coerce") / 1_048_576
    valid = ~(iops.isna() | throughput.isna())
    timestamps, iops, throughput = timestamps[valid], iops[valid], throughput[valid]
    if len(iops) == 0:
        raise ValueError("CSV contains no numeric IOPS/throughput samples.")

    p95_t, p95_i = np.percentile(throughput, 95), np.percentile(iops, 95)
    p99_t, p99_i = np.percentile(throughput, 99), np.percentile(iops, 99)

    def bursts(series, threshold):
        found = []
        active = False
        start = None
        for pos, value in enumerate(series.to_numpy()):
            if value > threshold and not active:
                start, active = timestamps.iloc[pos], True
            elif value <= threshold and active:
                end = timestamps.iloc[pos]
                found.append((str(start), str(end), (end - start).total_seconds()))
                active = False
        if active:
            end = timestamps.iloc[-1]
            found.append((str(start), str(end), (end - start).total_seconds()))
        return found

    return {
        "Baseline throughput min (MB/s)": float(throughput.min()),
        "Baseline throughput max (MB/s)": float(throughput.max()),
        "Baseline throughput avg (MB/s)": float(throughput.mean()),
        "Baseline IOPS min/s": float(iops.min()),
        "Baseline IOPS max/s": float(iops.max()),
        "Baseline IOPS avg/s": float(iops.mean()),
        "Recommended throughput P95 (MB/s)": float(p95_t),
        "Recommended IOPS P95": float(p95_i),
        "Recommended throughput P99 / microburst (MB/s)": float(p99_t),
        "Recommended IOPS P99 / microburst": float(p99_i),
        "Recommended throughput ceiling +10% (MB/s)": float(throughput.max() * 1.1),
        "Recommended IOPS ceiling +10%": float(iops.max() * 1.1),
        "Throughput microbursts": bursts(throughput, p99_t),
        "IOPS microbursts": bursts(iops, p99_i),
    }
