#!/usr/bin/env python3
"""
eBird Alert Scraper
Scrapes rare bird sightings from eBird alerts using Playwright.
"""

import json
import os
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


def login_to_ebird(page, username: str, password: str) -> bool:
    """Login to eBird with provided credentials."""
    print("Navigating to eBird...")
    page.goto("https://ebird.org/home")

    # Click sign in link
    try:
        page.click('a[href*="login"], .Header-link--signIn, text="Sign in"', timeout=10000)
    except Exception:
        # May already be on login page or logged in
        pass

    # Wait for login form
    page.wait_for_selector('input[name="username"], input[type="email"], #input-user-name', timeout=15000)

    # Fill credentials
    username_input = page.query_selector('input[name="username"], input[type="email"], #input-user-name')
    password_input = page.query_selector('input[name="password"], input[type="password"], #input-password')

    if username_input and password_input:
        username_input.fill(username)
        password_input.fill(password)

        # Submit form
        submit_btn = page.query_selector('button[type="submit"], input[type="submit"], .Button--primary')
        if submit_btn:
            submit_btn.click()
        else:
            page.keyboard.press("Enter")

        # Wait for redirect after login
        page.wait_for_load_state("networkidle", timeout=30000)
        print("Login successful!")
        return True

    return False


def scrape_alerts(page) -> list[dict]:
    """Scrape bird sightings from the alerts page."""
    print(f"Navigating to alerts: {ALERT_URL}")
    page.goto(ALERT_URL)
    page.wait_for_load_state("networkidle", timeout=30000)

    # Wait for content to load
    page.wait_for_selector('.Observation, .sighting, table, .ResultsStats', timeout=30000)

    html = page.content()
    soup = BeautifulSoup(html, "lxml")

    sightings = []
    scrape_timestamp = datetime.utcnow().isoformat() + "Z"

    # Try multiple selectors for different page structures
    # eBird uses various layouts for alerts

    # Method 1: Look for observation rows in tables
    for row in soup.select('tr.Observation-row, tr[data-species], .Observation'):
        sighting = parse_observation_row(row, scrape_timestamp)
        if sighting:
            sightings.append(sighting)

    # Method 2: Look for species sections
    if not sightings:
        for section in soup.select('.Observation-species, .species-section, [class*="species"]'):
            sighting = parse_species_section(section, scrape_timestamp)
            if sighting:
                sightings.append(sighting)

    # Method 3: Generic parsing of any structured data
    if not sightings:
        sightings = parse_generic_alerts(soup, scrape_timestamp)

    print(f"Found {len(sightings)} sightings")
    return sightings


def parse_observation_row(row, timestamp: str) -> dict | None:
    """Parse a single observation row."""
    try:
        species_el = row.select_one('.Observation-species, .species-name, a[href*="species"]')
        location_el = row.select_one('.Observation-location, .location, a[href*="hotspot"]')
        date_el = row.select_one('.Observation-date, .date, time')
        observer_el = row.select_one('.Observation-observer, .observer, a[href*="profile"]')
        count_el = row.select_one('.Observation-count, .count')

        species = species_el.get_text(strip=True) if species_el else None
        if not species:
            return None

        sighting = {
            "species": species,
            "location": location_el.get_text(strip=True) if location_el else "Unknown",
            "date": date_el.get_text(strip=True) if date_el else "Unknown",
            "observer": observer_el.get_text(strip=True) if observer_el else "Unknown",
            "count": count_el.get_text(strip=True) if count_el else "1",
            "scraped_at": timestamp,
        }

        # Extract links if available
        if species_el and species_el.get('href'):
            sighting["species_url"] = "https://ebird.org" + species_el['href'] if species_el['href'].startswith('/') else species_el['href']
        if location_el and location_el.get('href'):
            sighting["location_url"] = "https://ebird.org" + location_el['href'] if location_el['href'].startswith('/') else location_el['href']

        sighting["id"] = generate_sighting_id(sighting)
        return sighting
    except Exception as e:
        print(f"Error parsing row: {e}")
        return None


def parse_species_section(section, timestamp: str) -> dict | None:
    """Parse a species section element."""
    try:
        text = section.get_text(" ", strip=True)
        if len(text) < 3:
            return None

        sighting = {
            "species": text[:100],  # Limit length
            "location": "See details",
            "date": "Recent",
            "observer": "Unknown",
            "count": "1",
            "scraped_at": timestamp,
        }
        sighting["id"] = generate_sighting_id(sighting)
        return sighting
    except Exception:
        return None


def parse_generic_alerts(soup, timestamp: str) -> list[dict]:
    """Generic fallback parser for alert data."""
    sightings = []

    # Look for any links that might be species
    for link in soup.select('a[href*="/species/"], a[href*="speciesCode"]'):
        species = link.get_text(strip=True)
        if species and len(species) > 2:
            sighting = {
                "species": species,
                "location": "Unknown",
                "date": "Recent",
                "observer": "Unknown",
                "count": "1",
                "scraped_at": timestamp,
                "species_url": "https://ebird.org" + link['href'] if link['href'].startswith('/') else link['href'],
            }
            sighting["id"] = generate_sighting_id(sighting)
            sightings.append(sighting)

    return sightings


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

    # Sort by scrape date, newest first
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
        .location-link {
            color: #666;
            text-decoration: none;
        }
        .location-link:hover {
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
                    <th>Scraped</th>
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
                    </td>
                    <td>
                        {% if s.location_url %}
                        <a href="{{ s.location_url }}" target="_blank" class="location-link">{{ s.location }}</a>
                        {% else %}
                        {{ s.location }}
                        {% endif %}
                    </td>
                    <td>{{ s.date }}</td>
                    <td>{{ s.observer }}</td>
                    <td>{{ s.count }}</td>
                    <td>{{ s.scraped_at[:10] if s.scraped_at else 'N/A' }}</td>
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
                order: [[5, 'desc']],  // Sort by scraped date, newest first
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
    username = os.environ.get("EBIRD_USERNAME")
    password = os.environ.get("EBIRD_PASSWORD")

    if not username or not password:
        print("Error: EBIRD_USERNAME and EBIRD_PASSWORD environment variables required")
        print("Set them with: export EBIRD_USERNAME='your_email' EBIRD_PASSWORD='your_password'")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = context.new_page()

        try:
            if not login_to_ebird(page, username, password):
                print("Error: Failed to login to eBird")
                return 1

            new_sightings = scrape_alerts(page)

            if new_sightings:
                existing = load_existing_sightings()
                merged = merge_sightings(existing, new_sightings)
                save_sightings(merged)
                generate_html(merged)
            else:
                print("No sightings found - generating HTML with existing data")
                existing = load_existing_sightings()
                if existing:
                    generate_html(existing)
                else:
                    print("No existing data either - creating empty page")
                    generate_html([])

        finally:
            browser.close()

    return 0


if __name__ == "__main__":
    exit(main())
