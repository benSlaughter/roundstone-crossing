"""Camera footage analysis pipeline.

Processes time-lapse AVI recordings from the Ceyomur CY50 trail camera
and produces per-frame barrier/wig-wag detections plus a site-quality
score, for cross-referencing against the predictor's `predictions` table.

Usage:
    python -m experiments.camera_analysis run <site_dir>
    python -m experiments.camera_analysis sample <site_dir> [--at HH:MM]
    python -m experiments.camera_analysis preview-rois <site_dir>

See README.md alongside this module for details.
"""
