from dataclasses import dataclass
from datetime import date
from pathlib import Path

BASE_URL = "https://reserve.bcparks.ca/dayuse/"
API_URL = BASE_URL + "api/"
DEFAULT_STATE_PATH = Path(".auth/state.json")


@dataclass(frozen=True)
class Product:
    """Identifies a pass type, both in the reservation API and in the booking form.

    The IDs come from the site's API calls, e.g.
    api/inventoryPools/<collection>/dayuse/<activity>/<product>; the names are the
    menu labels on the booking form.
    """

    collection_id: str
    activity_id: int
    product_id: int
    park_name: str
    pass_name: str


RUBBLE_CREEK_VEHICLE = Product("bcparks_7", 3, 1, "Rubble Creek", "Day-use vehicle pass - DAY")


@dataclass(frozen=True)
class Vehicle:
    plate: str
    province: str = "British Columbia"


@dataclass(frozen=True)
class Settings:
    dates: tuple[date, ...]
    product: Product = RUBBLE_CREEK_VEHICLE
    vehicle: Vehicle | None = None  # set to book automatically
    dry_run: bool = False
    state_path: Path = DEFAULT_STATE_PATH
    headless: bool = True
    interval_seconds: int = 300
    once: bool = False
    timeout_ms: int = 30_000
