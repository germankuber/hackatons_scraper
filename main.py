import json
import os
import re
import time
import unicodedata
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from supabase import Client, create_client
from termcolor import cprint

# Load environment variables
load_dotenv()
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Setup headless browser
# options = Options()
# options.add_argument('--headless')
# options.add_argument('--disable-gpu')

options = Options()
options.add_argument("--headless=new")  # usar el modo headless moderno
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
driver = webdriver.Chrome(options=options)

base_url = "https://taikai.network"
list_url = f"{base_url}/en/hackathons"

emoji_pattern = re.compile("[\U00010000-\U0010ffff]", flags=re.UNICODE)


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # 1) Remove *all* Unicode symbols (emojis, dingbats like ⚡, currency signs, math symbols…)
    filtered = []
    for ch in text:
        # category starting with 'S' means Symbol
        if unicodedata.category(ch).startswith("S"):
            continue
        filtered.append(ch)
    text = "".join(filtered)
    # 2) Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def wait_for_element(by: By, value: str, timeout: int = 10) -> None:
    # time.sleep(1)  # Allow time for the page to load completely
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))


def extract_project_data(hackathon_id: int) -> None:
    # wait_for_element(By.CLASS_NAME, 'html-editor-body')
    time.sleep(2)  # Allow time for the page to load completely
    soup = BeautifulSoup(driver.page_source, "html.parser")

    title = ""
    title_container = soup.find("div", class_="iwSID")
    if title_container:
        h1 = title_container.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    description = ""
    body_div = soup.find("div", class_="html-editor-body")
    if body_div:
        description = body_div.get_text(strip=True, separator="\n")

    tags: List[str] = []
    for ul in soup.find_all("ul", class_="tags"):
        tags += [span.get_text(strip=True) for span in ul.find_all("span")]

    save_project_data(
        hackathon_id,
        {
            "title": title,
            "description": description,
            "tags": tags,
            "url": driver.current_url,
        },
    )


def fetch_challenges_page(page: int, cookies: str) -> List[Dict]:
    """Fetch one page of challenges (hackathons) from Taikai API."""
    url = "https://api.taikai.network/api/graphql"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "cookie": cookies,
        "referer": "https://taikai.network/",
        "origin": "https://taikai.network",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }

    payload = {
        "operationName": "ALL_CHALLENGES_QUERY",
        "variables": {
            "sortBy": {"order": "desc"},
            "searchTerm": "%%",
            "page": page,
        },
        "query": """
        query ALL_CHALLENGES_QUERY(
          $sortBy: ChallengeOrderByWithRelationInput,
          $searchTerm: String,
          $page: Int
        ) {
          challenges(
            where: {
              publishInfo: {state: {equals: ACTIVE}},
              OR: [
                {name: {contains: $searchTerm, mode: insensitive}},
                {slug: {contains: $searchTerm, mode: insensitive}},
                {organization: {name: {contains: $searchTerm, mode: insensitive}}}
              ]
            },
            page: $page,
            orderBy: $sortBy
          ) {
            id
            name
            slug
            isClosed
            organization {
              id
              name
              slug
            }
            industries {
                title
                }
          }
        }
        """,
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        cprint(
            f"[✖] Challenge fetch failed (page {page}): {response.status_code}", "red"
        )
        return []

    try:
        return response.json()["data"]["challenges"]
    except Exception as e:
        cprint(f"[✖] JSON parse error for challenges: {e}", "red")
        return []


def fetch_projects_for_challenge(challenge_id: str, cookies: str) -> List[Dict]:
    """Fetch all projects for a given challenge (hackathon) ID."""
    url = "https://api.taikai.network/api/graphql"
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "cookie": cookies,
        "referer": "https://taikai.network/",
        "origin": "https://taikai.network",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    }

    raw_body = """{"operationName":"PROJECTS_BY_CHALLENGE","variables":{"orderBy":{"name":"asc"},"page":0,"whereInput":{"state":{"in":["ACTIVE","DRAFT","NOT_ELIGIBLE"]},"name":{"contains":"","mode":"insensitive"},"challenge":{"id":{"equals":"__CHALLENGE_ID__"}}},"username":"german.kuber"},"query":"query PROJECTS_BY_CHALLENGE( $orderBy: ProjectOrderByWithRelationInput!, $whereInput: ProjectWhereInput, $page: Int) {\\n  projects(orderBy: $orderBy, where: $whereInput, page: $page) {\\n    id\\n    name\\n    teaser\\n    description\\n    state\\n    viewsCount\\n    favoritesCount\\n    backersCount\\n    totalBacked\\n  }\\n}\\n"}"""
    body = raw_body.replace("__CHALLENGE_ID__", challenge_id)

    response = requests.post(url, headers=headers, data=body)
    if response.status_code != 200:
        cprint(
            f"[✖] Project fetch failed for {challenge_id}: {response.status_code}",
            "red",
        )
        return []

    try:
        return response.json()["data"]["projects"]
    except Exception as e:
        cprint(f"[✖] JSON parse error for projects: {e}", "red")
        return []


def get_existing_hackathon_ids() -> Set[str]:
    """Fetch all stored hackathon external_ids from the database."""
    try:
        response = supabase.table("hackathons").select("external_id").execute()
        return {row["external_id"] for row in response.data} if response.data else set()
    except Exception as e:
        cprint(f"[✖] Failed to fetch existing hackathons: {e}", "red")
        return set()


def load_all_data():
    cookies = "PUT_REAL_COOKIES_HERE"
    stored_ids = get_existing_hackathon_ids()

    for page in range(20):
        challenges = fetch_challenges_page(page, cookies)
        if not challenges:
            continue

        for ch in challenges:
            if not ch.get("isClosed", False):
                continue

            if ch["id"] in stored_ids:
                cprint(f"⏭ Skipping already saved hackathon: {ch['name']}", "yellow")
                continue

            # Save hackathon
            industry_titles = [ind["title"] for ind in ch.get("industries", [])]

            hackathon_data = {
                "external_id": ch["id"],
                "organization_id": ch["organization"]["id"],
                "organization_name": ch["organization"]["name"],
                "organization_slug": ch["organization"]["slug"],
                "slug": ch.get("slug", ""),
                "name": clean_text(ch.get("name", "")),
                "industries": industry_titles,  # << only the titles
                "processed": False,
            }
            result = (
                supabase.table("hackathons")
                .upsert(hackathon_data, on_conflict=["external_id"])
                .execute()
            )
            hackathon_id = result.data[0]["id"]
            cprint(
                f"✔ Saved hackathon: {hackathon_data['name']} (DB ID: {hackathon_id})",
                "green",
            )

            # Fetch and save projects in batch
            projects = fetch_projects_for_challenge(ch["id"], cookies)
            project_records = []

            for p in projects:
                project_data = {
                    "external_id": p["id"],
                    "hackathon_id": hackathon_id,
                    "title": clean_text(p.get("name", "")),
                    "description": clean_text(p.get("description", "")),
                    "tags": [],
                    "url": f"https://taikai.network/project/{p['id']}",
                    "processed": False,
                }
                project_records.append(project_data)

            if project_records:
                supabase.table("projects").upsert(
                    project_records, on_conflict=["external_id"]
                ).execute()
                cprint(f"   ↳ {len(project_records)} projects inserted", "cyan")


def load_hackathons() -> None:
    """
    Load all hackathons from the DB where processed = False,
    navigate to each hackathon’s Overview page, scrape its
    description (and URL), update the DB record, and mark as processed.
    """
    # 1) Fetch pending hackathons (id + slug)
    resp = (
        supabase.table("hackathons")
        .select("id, slug, organization_slug")
        .eq("processed", False)
        .execute()
    )
    hackathons = resp.data or []

    for h in hackathons:
        hackathon_id = h["id"]
        slug = h.get("slug")
        organization_slug = h.get("organization_slug")

        if not slug:
            cprint(f"[✖] Missing slug for hackathon ID {hackathon_id}", "red")
            continue

        # 2) Build the hackathon URL and navigate there
        hackathon_url = f"{base_url}/{organization_slug}/hackathons/{slug}"
        print(f"🔗 Navigating to {hackathon_url}")
        driver.get(hackathon_url)
        time.sleep(2)  # Allow time for the page to load completely

        # 3) Click into the "Overview" tab
        soup = BeautifulSoup(driver.page_source, "html.parser")
        body_div = soup.find("div", class_="html-editor-body")
        description = clean_text(body_div.get_text(separator="\n")) if body_div else ""

        # 5) Update DB record: set url, description, processed = True
        try:
            supabase.table("hackathons").update(
                {"url": hackathon_url, "description": description, "processed": True}
            ).eq("id", hackathon_id).execute()
            cprint(f"[✔] Updated hackathon '{slug}' (ID {hackathon_id})", "green")
        except Exception as e:
            cprint(f"[✖] Failed to update hackathon '{slug}': {e}", "red")


if __name__ == "__main__":
    try:
        cprint("🚀 Starting hackathon scraper...\n", "cyan", attrs=["bold"])
        # load_all_data()
        load_hackathons()
    finally:
        driver.quit()
        cprint("\n🧹 Browser session closed", "white", attrs=["dark"])
