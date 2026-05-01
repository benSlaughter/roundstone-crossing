# Manual Crossing Observations

## Accuracy Notes

All observations are recorded manually via phone shortcuts. Human factors
that affect accuracy:

- **Reaction time**: ~1-3s delay between observing an event and pressing the button
- **Attention**: observer may be driving, walking, or distracted
- **Compounding**: derived timings (e.g. train-to-opening) combine two
  imprecise measurements, so error can be ±5-10s
- **Precision column**: "minute" = ±30s, "second" = ±3-5s (not ±1s)

## Calibration Guidelines

- Use **median** values across multiple observations, not individual readings
- Treat outliers (e.g. unusually short/long intervals) with low confidence
- Prefer observations where multiple second-precision events bracket a
  timing interval (e.g. closing → train → opening in one session)
- Device-logged data (when available) will be far more accurate (~100ms)

## Format

```
timestamp,event,precision,notes
```

- **timestamp**: ISO 8601, local time (Europe/London)
- **event**: open, closed, closing, opening, train_east, train_west
- **precision**: "minute" or "second"
- **notes**: context about the observation
