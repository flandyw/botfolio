"""Optional purged expanding-window classifier. Outcomes: positive after-cost fixed-window return."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["materiality_high", "bullish", "bearish", "surprise_positive", "surprise_negative",
            "return_5m", "volatility", "volume_ratio", "hour_utc"]


def walk_forward(frame: pd.DataFrame, folds=3):
    import lightgbm as lgb

    required = {*FEATURES, "event", "entry", "exit", "net_return"}
    if not required.issubset(frame.columns) or frame.event.duplicated().any():
        raise ValueError("require one row per event, features, entry/exit and net_return")
    data = frame.sort_values("entry").copy()
    data["entry"] = pd.to_datetime(data.entry, utc=True)
    data["exit"] = pd.to_datetime(data.exit, utc=True)
    if len(data) < 40 or folds < 1 or (data.exit <= data.entry).any():
        raise ValueError("need >=40 events with valid outcome windows")
    # Split whole dates together to avoid the same market shock appearing in train and test.
    days = sorted(data.entry.dt.date.unique())
    chunks = np.array_split(days, folds + 1)
    if any(len(chunk) == 0 for chunk in chunks):
        raise ValueError("insufficient dates for walk-forward folds")
    outputs = []
    for chunk in chunks[1:]:
        test = data[data.entry.dt.date.isin(chunk)]
        train = data[data.exit < test.entry.min()]
        y = (train.net_return > 0).astype(int)
        if len(train) < 20 or y.nunique() < 2:
            continue
        model = lgb.train(dict(objective="binary", max_depth=2, num_leaves=4, learning_rate=.05,
                               seed=42, num_threads=1, verbosity=-1),
                          lgb.Dataset(train[FEATURES].astype(float), label=y), num_boost_round=50)
        probability = model.predict(test[FEATURES].astype(float))
        actual = (test.net_return > 0).astype(int).to_numpy()
        outputs.append(dict(n_train=len(train), n_test=len(test), train_last_exit=train.exit.max().isoformat(),
            test_first_entry=test.entry.min().isoformat(), brier=float(np.mean((probability-actual)**2)),
            accuracy=float(np.mean((probability >= .5) == actual)),
            calibration=[dict(lower=low, n=int(((probability >= low) & (probability < low+.2)).sum()),
                predicted=float(probability[(probability >= low) & (probability < low+.2)].mean()),
                observed=float(actual[(probability >= low) & (probability < low+.2)].mean()))
                for low in (0., .2, .4, .6, .8) if ((probability >= low) & (probability < low+.2)).any()],
            predictions=[dict(event=e, probability=float(p), actual=int(a))
                         for e, p, a in zip(test.event, probability, actual, strict=True)]))
    return outputs
