import sys
import types
from pathlib import Path

# Ensure third-party Kafka bindings are stubbed if unavailable.
try:
    import confluent_kafka  # type: ignore
except ImportError:  # pragma: no cover - executed only in test env without lib
    class _DummyProducer:
        def __init__(self, *args, **kwargs):
            pass

        def produce(self, *args, **kwargs):
            pass

        def poll(self, *args, **kwargs):
            return None

        def flush(self, *args, **kwargs):
            pass

    class _DummyConsumer:
        def __init__(self, *args, **kwargs):
            pass

        def subscribe(self, *args, **kwargs):
            pass

        def poll(self, *args, **kwargs):
            return None

        def close(self):
            pass

    sys.modules["confluent_kafka"] = types.SimpleNamespace(
        Producer=_DummyProducer, Consumer=_DummyConsumer
    )

# Ensure the src/ directory is importable in tests.
ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
