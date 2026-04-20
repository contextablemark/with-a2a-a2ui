# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os

logger = logging.getLogger(__name__)


def _public_base_url() -> str:
    """Host under which the browser/client fetches `/static/*` images.

    On Railway the domain is provided; locally fall back to the port the
    Node runtime serves on (images are mounted at /static/ there).
    """
    if domain := os.getenv("RAILWAY_PUBLIC_DOMAIN"):
        return f"https://{domain}"
    return os.getenv("PUBLIC_URL") or f"http://localhost:{os.getenv('PORT', '3000')}"


def get_restaurants(cuisine: str, location: str, count: int = 5) -> str:
    """Call this tool to get a list of restaurants based on a cuisine and location.
    'count' is the number of restaurants to return.
    """
    logger.info(f"--- TOOL CALLED: get_restaurants (count: {count}) ---")
    logger.info(f"  - Cuisine: {cuisine}")
    logger.info(f"  - Location: {location}")

    items = []
    if "new york" in location.lower() or "ny" in location.lower():
        try:
            script_dir = os.path.dirname(__file__)
            file_path = os.path.join(script_dir, "restaurant_data.json")
            with open(file_path) as f:
                restaurant_data_str = f.read()
            base_url = _public_base_url()
            restaurant_data_str = restaurant_data_str.replace(
                "http://localhost:10002", base_url
            )
            all_items = json.loads(restaurant_data_str)
            items = all_items[:count]
            logger.info(
                f"  - Success: Found {len(all_items)} restaurants, returning {len(items)}."
            )
        except FileNotFoundError:
            logger.error(f"  - Error: restaurant_data.json not found at {file_path}")
        except json.JSONDecodeError:
            logger.error(f"  - Error: Failed to decode JSON from {file_path}")

    return json.dumps(items)
