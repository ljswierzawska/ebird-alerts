#!/usr/bin/env python3
"""
eBird Alert Scraper
Scrapes rare bird sightings from eBird alerts using Playwright.
No login required - the alert summary page is publicly accessible.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from jinja2 import Template

# Configuration
ALERT_URL = "https://ebird.org/alert/summary?sid=SN35466"
DATA_DIR = Path("data")
SIGHTINGS_FILE = DATA_DIR / "sightings.json"
OUTPUT_HTML = Path("index.html")


def generate_sighting_id(sighting: dict) -> str:
    """Generate a unique ID for a sighting based on its content."""
    unique_str = f"{sighting['species']}_{sighting['location']}_{sighting['date']}_{sighting.get('observer', '')}"
    return hashlib.md5(unique_str.encode()).hexdigest()[:12]


def scrape_alerts(page) -> list[dict]:
    """Scrape bird sightings from the alerts page."""
    print(f"Navigating to alerts: {ALERT_URL}")
    page.goto(ALERT_URL, wait_until="networkidle", timeout=60000)

    # Wait for content to load
    print("Waiting for page content...")
    page.wait_for_selector('.Observation', timeout=30000)

    # Give JavaScript time to render
    page.wait_for_timeout(3000)

    html = page.content()
    soup = BeautifulSoup(html, "lxml")

    sightings = []
    scrape_timestamp = datetime.utcnow().isoformat() + "Z"

    # Find all observation divs
    observations = soup.select('div.Observation')
    print(f"Found {len(observations)} observation elements")

    for obs in observations:
        sighting = parse_observation(obs, scrape_timestamp)
        if sighting:
            sightings.append(sighting)

    print(f"Parsed {len(sightings)} sightings")
    return sightings


def parse_observation(obs, timestamp: str) -> dict | None:
    """Parse a single eBird observation div."""
    try:
        # Species name - from .Heading-main
        species_el = obs.select_one('.Observation-species .Heading-main')
        if not species_el:
            return None
        species = species_el.get_text(strip=True)

        # Scientific name - from .Heading-sub
        sci_name_el = obs.select_one('.Observation-species .Heading-sub')
        sci_name = sci_name_el.get_text(strip=True) if sci_name_el else ""

        # Species URL
        species_link = obs.select_one('.Observation-species a[data-species-code]')
        species_url = ""
        if species_link and species_link.get('href'):
            href = species_link['href']
            species_url = "https://ebird.org" + href if href.startswith('/') else href

        # Count - from .Observation-numberObserved
        count_el = obs.select_one('.Observation-numberObserved span')
        count = count_el.get_text(strip=True) if count_el else "1"

        # Date - from link to checklist
        date_el = obs.select_one('.Observation-meta a[href*="/checklist/"]')
        date = date_el.get_text(strip=True) if date_el else "Unknown"

        # Checklist URL
        checklist_url = ""
        if date_el and date_el.get('href'):
            href = date_el['href']
            checklist_url = "https://ebird.org" + href if href.startswith('/') else href

        # Location - from Google Maps link
        location_el = obs.select_one('.Observation-meta a[href*="google.com/maps"]')
        location = location_el.get_text(strip=True) if location_el else "Unknown"
        location_url = location_el['href'] if location_el and location_el.get('href') else ""

        # Observer - from the GridFlex-cell containing user icon
        # The observer is in the third GridFlex-cell in the meta section
        meta_cells = obs.select('.Observation-meta .GridFlex-cell.u-md-size1of4')
        observer = "Unknown"
        for cell in meta_cells:
            # Check if this cell contains the user icon
            if cell.select_one('svg.Icon--user, [class*="Icon--user"]'):
                observer_span = cell.select_one('.u-sizeFill span:not(.is-visuallyHidden)')
                if observer_span:
                    observer = observer_span.get_text(strip=True)
                break

        # If we didn't find observer via icon, try last cell in grid
        if observer == "Unknown":
            grid_cells = obs.select('.Observation-meta .GridFlex > .GridFlex-cell')
            if len(grid_cells) >= 3:
                last_cell = grid_cells[-1]
                observer_span = last_cell.select_one('.u-sizeFill span:not(.is-visuallyHidden)')
                if observer_span:
                    observer = observer_span.get_text(strip=True)

        sighting = {
            "species": species,
            "scientific_name": sci_name,
            "location": location,
            "date": date,
            "observer": observer,
            "count": count,
            "scraped_at": timestamp,
            "species_url": species_url,
            "location_url": location_url,
            "checklist_url": checklist_url,
        }

        sighting["id"] = generate_sighting_id(sighting)
        return sighting

    except Exception as e:
        print(f"Error parsing observation: {e}")
        return None


def load_existing_sightings() -> list[dict]:
    """Load existing sightings from JSON file."""
    if SIGHTINGS_FILE.exists():
        with open(SIGHTINGS_FILE, "r") as f:
            return json.load(f)
    return []


def save_sightings(sightings: list[dict]) -> None:
    """Save sightings to JSON file."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(SIGHTINGS_FILE, "w") as f:
        json.dump(sightings, f, indent=2)
    print(f"Saved {len(sightings)} sightings to {SIGHTINGS_FILE}")


def merge_sightings(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new sightings with existing, avoiding duplicates."""
    existing_ids = {s["id"] for s in existing}
    merged = existing.copy()

    added = 0
    for sighting in new:
        if sighting["id"] not in existing_ids:
            merged.append(sighting)
            existing_ids.add(sighting["id"])
            added += 1

    print(f"Added {added} new sightings")
    return merged


def generate_html(sightings: list[dict]) -> None:
    """Generate the HTML page with DataTables."""
    template = Template(HTML_TEMPLATE)

    # Sort by date, newest first
    sorted_sightings = sorted(
        sightings,
        key=lambda x: x.get("scraped_at", ""),
        reverse=True
    )

    html = template.render(
        sightings=sorted_sightings,
        total_count=len(sightings),
        last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    with open(OUTPUT_HTML, "w") as f:
        f.write(html)
    print(f"Generated {OUTPUT_HTML}")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>eBird Rare Bird Alerts</title>
    <link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css">
    <style>
        * {
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #2c5530;
            margin-top: 0;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        h1::before {
            content: "🐦";
        }
        .stats {
            background: #e8f5e9;
            padding: 15px 20px;
            border-radius: 6px;
            margin-bottom: 20px;
            display: flex;
            gap: 30px;
            flex-wrap: wrap;
        }
        .stat {
            display: flex;
            flex-direction: column;
        }
        .stat-value {
            font-size: 24px;
            font-weight: bold;
            color: #2c5530;
        }
        .stat-label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
        }
        table.dataTable {
            width: 100% !important;
        }
        table.dataTable thead th {
            background: #2c5530;
            color: white;
        }
        table.dataTable tbody tr:hover {
            background: #f0f7f0 !important;
        }
        .species-link {
            color: #1a73e8;
            text-decoration: none;
            font-weight: 500;
        }
        .species-link:hover {
            text-decoration: underline;
        }
        .scientific-name {
            color: #888;
            font-style: italic;
            font-size: 0.9em;
        }
        .location-link {
            color: #666;
            text-decoration: none;
        }
        .location-link:hover {
            color: #1a73e8;
        }
        .date-link {
            color: #333;
            text-decoration: none;
        }
        .date-link:hover {
            color: #1a73e8;
        }
        footer {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #eee;
            font-size: 12px;
            color: #888;
            text-align: center;
        }
        @media (max-width: 768px) {
            body {
                padding: 10px;
            }
            .container {
                padding: 15px;
            }
            .stats {
                flex-direction: column;
                gap: 15px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>eBird Rare Bird Alerts</h1>

        <div class="stats">
            <div class="stat">
                <span class="stat-value">{{ total_count }}</span>
                <span class="stat-label">Total Sightings</span>
            </div>
            <div class="stat">
                <span class="stat-value">{{ sightings | map(attribute='species') | unique | list | length }}</span>
                <span class="stat-label">Unique Species</span>
            </div>
            <div class="stat">
                <span class="stat-value">{{ last_updated }}</span>
                <span class="stat-label">Last Updated</span>
            </div>
        </div>

        <table id="sightings-table" class="display">
            <thead>
                <tr>
                    <th>Species</th>
                    <th>Location</th>
                    <th>Date</th>
                    <th>Observer</th>
                    <th>Count</th>
                </tr>
            </thead>
            <tbody>
                {% for s in sightings %}
                <tr>
                    <td>
                        {% if s.species_url %}
                        <a href="{{ s.species_url }}" target="_blank" class="species-link">{{ s.species }}</a>
                        {% else %}
                        {{ s.species }}
                        {% endif %}
                        {% if s.scientific_name %}
                        <br><span class="scientific-name">{{ s.scientific_name }}</span>
                        {% endif %}
                    </td>
                    <td>
                        {% if s.location_url %}
                        <a href="{{ s.location_url }}" target="_blank" class="location-link">{{ s.location }}</a>
                        {% else %}
                        {{ s.location }}
                        {% endif %}
                    </td>
                    <td>
                        {% if s.checklist_url %}
                        <a href="{{ s.checklist_url }}" target="_blank" class="date-link">{{ s.date }}</a>
                        {% else %}
                        {{ s.date }}
                        {% endif %}
                    </td>
                    <td>{{ s.observer }}</td>
                    <td>{{ s.count }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <footer>
            Data scraped from <a href="https://ebird.org">eBird</a> |
            Auto-updated daily via GitHub Actions
        </footer>
    </div>

    <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
    <script>
        $(document).ready(function() {
            $('#sightings-table').DataTable({
                pageLength: 25,
                order: [[2, 'desc']],  // Sort by date, newest first
                responsive: true,
                language: {
                    search: "Filter:",
                    lengthMenu: "Show _MENU_ sightings",
                    info: "Showing _START_ to _END_ of _TOTAL_ sightings",
                }
            });
        });
    </script>
</body>
</html>
"""


def main():
    """Main entry point."""
    print("Starting eBird Alert Scraper (no login required)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            new_sightings = scrape_alerts(page)

            if new_sightings:
                # Start fresh with new data structure
                save_sightings(new_sightings)
                generate_html(new_sightings)
            else:
                print("No sightings found - generating empty page")
                generate_html([])

        finally:
            browser.close()

    print("Done!")
    return 0


if __name__ == "__main__":
    exit(main())
