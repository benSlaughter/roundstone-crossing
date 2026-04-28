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
from pathlib import Path
from time import sleep

import yaml

logger = logging.getLogger("crossing")


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_predictor(config: dict, with_api: bool = False):
    """Main loop: connect to feeds, track trains, infer state, log history."""
    from .tracker import TrainTracker
    from .inferrer import CrossingInferrer
    from .history import HistoryLogger
    from .feed import NRODFeed
    from .rtt import RTTClient

    tracker = TrainTracker(config)
    inferrer = CrossingInferrer(config)
    history = HistoryLogger()
    tracker.history = history

    last_feed_time = None

    def on_feed_message(ts: datetime):
        nonlocal last_feed_time
        last_feed_time = ts

    feed = NRODFeed(tracker, on_message_callback=on_feed_message)

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
    logger.info("🚂 Roundstone Crossing Predictor starting...")
    if not feed.start():
        logger.error("Failed to connect to NROD. Check credentials in .env")
        sys.exit(1)

    logger.info("🟢 Running — press Ctrl+C to stop")
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
                print(
                    f"  🚦 {status.state.value.upper()}"
                    f" ({status.confidence:.0%}){train_str}{eta_str}"
                )

            # Log completed train passages
            for train in list(tracker.trains.values()):
                from .models import TrainPhase
                if train.phase == TrainPhase.CLEARED and not getattr(train, '_passage_logged', False):
                    history.log_train_passage(train)
                    train._passage_logged = True

            sleep(2)  # Check every 2 seconds

    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
    finally:
        rtt.stop()
        feed.stop()
        logger.info("🔴 Stopped")


def _start_api(config: dict, tracker, inferrer, history, rtt_client=None):
    """Start the FastAPI server."""
    import uvicorn
    from .api import create_app

    app = create_app(tracker, inferrer, history, rtt_client=rtt_client)
    api_config = config.get("api", {})
    host = api_config.get("host", "0.0.0.0")
    port = api_config.get("port", 8590)
    logger.info(f"🌐 API on {host}:{port}")
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

    # Logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    # Quiet noisy libs
    for noisy in ("stomp.py", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    config = load_config()
    run_predictor(config, with_api=args.api)


if __name__ == "__main__":
    main()
