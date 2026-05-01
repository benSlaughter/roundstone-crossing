"""
Roundstone Crossing Predictor — main entry point.
Connects to NROD feeds, tracks trains, infers crossing state, serves API.
"""

import argparse
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep

import yaml

logger = logging.getLogger("crossing")


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def validate_config(config: dict) -> None:
    """Validate that required config keys exist and have sensible values.

    Raises SystemExit with a clear message on validation failure.
    """
    errors: list[str] = []

    # crossing.name
    if not config.get("crossing", {}).get("name"):
        errors.append("crossing.name is required")

    # td.area_id
    if not config.get("td", {}).get("area_id"):
        errors.append("td.area_id is required")

    # railway.stations.west/east with crs codes
    stations = config.get("railway", {}).get("stations", {})
    for side in ("west", "east"):
        station = stations.get(side, {})
        if not station:
            errors.append(f"railway.stations.{side} is required")
        elif not station.get("crs"):
            errors.append(f"railway.stations.{side}.crs is required")

    # timing values must exist and be positive numbers
    timing = config.get("timing", {})
    for key in ("pre_closure_secs", "crossing_clearance_secs", "post_clearance_secs"):
        val = timing.get(key)
        if val is None:
            errors.append(f"timing.{key} is required")
        elif not isinstance(val, (int, float)) or val <= 0:
            errors.append(f"timing.{key} must be a positive number, got {val!r}")

    # api.port must exist and be a valid port number
    port = config.get("api", {}).get("port")
    if port is None:
        errors.append("api.port is required")
    elif not isinstance(port, int) or not (1 <= port <= 65535):
        errors.append(f"api.port must be an integer between 1 and 65535, got {port!r}")

    if errors:
        raise SystemExit("Config validation failed:\n  - " + "\n  - ".join(errors))


def run_predictor(config: dict, with_api: bool = False):
    """Main loop: connect to feeds, track trains, infer state, log history."""
    from .tracker import TrainTracker
    from .inferrer import CrossingInferrer
    from .history import HistoryLogger
    from .feed import NRODFeed
    from .rtt import RTTClient
    from .models import TrainPhase

    tracker = TrainTracker(config)
    inferrer = CrossingInferrer(config)
    history = HistoryLogger()
    tracker.history = history

    last_feed_time = None

    def on_feed_message(ts: datetime):
        nonlocal last_feed_time
        last_feed_time = ts

    feed = NRODFeed(tracker, history=history, on_message_callback=on_feed_message)

    # Start RTT polling
    rtt_config = config.get("rtt", {})
    rtt_stations = rtt_config.get("stations", ["ANG", "GBS"])
    rtt_interval = rtt_config.get("poll_interval", 15)
    rtt = RTTClient(stations=rtt_stations, poll_interval=rtt_interval)
    rtt.set_callback(lambda **kw: tracker.handle_rtt_update(**{
        k: v for k, v in kw.items()
        if k in ("headcode", "station", "platform", "status", "origin_codes", "dest_codes")
    }))
    rtt.set_active_check(lambda: len(tracker.trains) > 0)
    rtt.start()

    # Start API server in background if requested
    if with_api:
        api_thread = threading.Thread(
            target=_start_api, args=(config, tracker, inferrer, history, rtt), daemon=True
        )
        api_thread.start()

    # Connect to NROD
    logger.info("Roundstone Crossing Predictor starting...")
    if not feed.start():
        logger.error("Failed to connect to NROD. Check credentials in .env")
        sys.exit(1)

    logger.info("Running — press Ctrl+C to stop")
    prev_state = None

    try:
        while True:
            # Get active trains and update crossing state
            active = tracker.get_active_trains()
            status = inferrer.update(active, last_feed_time)

            # Log state changes
            if status.state != prev_state:
                history.log_state_change(status)
                prev_state = status.state

                # Print status to terminal
                eta = status.seconds_until_change()
                eta_str = f" (change in {eta:.0f}s)" if eta else ""
                train_str = f" [{len(active)} train{'s' if len(active) != 1 else ''}]" if active else ""
                logger.info(
                    f"State: {status.state.value.upper()}"
                    f" ({status.confidence:.0%}){train_str}{eta_str}"
                )

            # Log completed train passages (take lock for thread safety)
            with tracker._lock:
                for train in list(tracker.trains.values()):
                    if train.phase == TrainPhase.CLEARED and not train._passage_logged:
                        history.log_train_passage(train)
                        train._passage_logged = True

            sleep(2)  # Check every 2 seconds

    except KeyboardInterrupt:
        print("\nShutting down...")
        logger.info("Shutting down...")
    finally:
        rtt.stop()
        feed.stop()
        logger.info("Stopped")


def _start_api(config: dict, tracker, inferrer, history, rtt_client=None):
    """Start the FastAPI server."""
    import uvicorn
    from .api import create_app

    app = create_app(tracker, inferrer, history, rtt_client=rtt_client)
    api_config = config.get("api", {})
    host = os.environ.get("API_HOST", api_config.get("host", "0.0.0.0"))
    port = api_config.get("port", 8590)
    logger.info(f"API on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description="Roundstone Crossing Predictor")
    parser.add_argument("--api", action="store_true", help="Start API server alongside predictor")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Load .env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    # Logging — console + rotating file
    log_level = logging.DEBUG if args.debug else logging.INFO
    log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    formatter = logging.Formatter(log_format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # File handler — rotates at 5MB, keeps 5 backups
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "crossing.log", maxBytes=5 * 1024 * 1024, backupCount=5,
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Redirect uvicorn/starlette access logs to file too
    for uvi_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvi_logger = logging.getLogger(uvi_name)
        uvi_logger.handlers.clear()
        uvi_logger.addHandler(console_handler)
        uvi_logger.addHandler(file_handler)
    # Quiet noisy libs
    for noisy in ("stomp.py", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    config = load_config()
    validate_config(config)
    run_predictor(config, with_api=args.api)


if __name__ == "__main__":
    main()
