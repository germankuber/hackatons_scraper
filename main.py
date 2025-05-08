import os
import re
import time
import unicodedata
from typing import Dict, List, Optional
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
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Setup headless Chrome
options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
driver = webdriver.Chrome(options=options)

base_url = "https://taikai.network"


def clean_text(text: Optional[str]) -> str:
    """Remove all Unicode symbols and collapse whitespace."""
    if not text:
        return ""
    filtered = [ch for ch in text if not unicodedata.category(ch).startswith("S")]
    return re.sub(r"\s+", " ", "".join(filtered)).strip()


def wait_for_element(by: By, value: str, timeout: int = 10) -> None:
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))


def save_hackathon_data(slug: str, data: Dict[str, str]) -> Optional[int]:
    cleaned = {
        "slug": slug,
        "url": clean_text(data.get("url")),
        "description": clean_text(data.get("description")),
        "processed": False,
    }
    resp = supabase.table("hackathons").upsert(cleaned, on_conflict=["slug"]).execute()
    if resp.data and isinstance(resp.data, list) and "id" in resp.data[0]:
        hid = resp.data[0]["id"]
        cprint(f"[✔] Hackathon '{slug}' saved (ID {hid})", "green")
        return hid
    else:
        cprint(f"[✖] Could not save hackathon '{slug}'", "red")
        return None


def mark_hackathon_processed(hackathon_id: int) -> None:
    supabase.table("hackathons").update({"processed": True}).eq(
        "id", hackathon_id
    ).execute()
    cprint(f"   ↳ Hackathon ID {hackathon_id} marked processed", "green")


def extract_data_from_hackathon(hackathon_id: int) -> None:
    """Scrape all project links on current page, then recurse through pagination."""
    while True:
        wait_for_element(By.CSS_SELECTOR, "div.gFHDc")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        project_divs = soup.select("div.gFHDc")
        cprint(f"   → Found {len(project_divs)} projects", "yellow")

        for div in project_divs:
            link = div.find("a", href=True)
            if not link:
                continue
            driver.get(urljoin(driver.current_url, link["href"]))
            time.sleep(2)
            # reuse your existing extract_project_data logic here:
            extract_project_data(hackathon_id)
            driver.back()
            wait_for_element(By.CSS_SELECTOR, "div.gFHDc")

        next_btn = soup.select_one("ul.pagination li.next a")
        if not next_btn or next_btn.get("aria-disabled") == "true":
            break
        driver.execute_script(
            "arguments[0].click()",
            driver.find_element(By.CSS_SELECTOR, "ul.pagination li.next a"),
        )
        time.sleep(2)


def extract_project_data(hackathon_id: int) -> None:
    """Extract title, description, tags, url and save to DB."""
    soup = BeautifulSoup(driver.page_source, "html.parser")

    title = (
        soup.find("div", class_="iwSID").get_text(strip=True)
        if soup.find("div", class_="iwSID")
        else ""
    )
    desc_div = soup.find("div", class_="html-editor-body")
    description = desc_div.get_text(separator="\n", strip=True) if desc_div else ""
    tags = [
        span.get_text(strip=True)
        for ul in soup.find_all("ul", class_="tags")
        for span in ul.find_all("span")
    ]
    url = driver.current_url

    cleaned = {
        "hackathon_id": hackathon_id,
        "title": clean_text(title),
        "description": clean_text(description),
        "tags": sorted(set(clean_text(t) for t in tags)),
        "url": clean_text(url),
    }
    supabase.table("projects").insert(cleaned).execute()
    cprint(f"   ↳ Project '{cleaned['title'][:50]}' saved", "cyan")


def load_all_data(cookies: str) -> None:
    """Fetch challenges via GraphQL, save hackathons + projects in batch."""
    existing = {
        row["external_id"]
        for row in supabase.table("hackathons").select("external_id").execute().data
        or []
    }

    for page in range(20):
        challenges = fetch_challenges_page(page, cookies)
        if not challenges:
            continue

        for ch in challenges:
            if not ch.get("isClosed") or ch["id"] in existing:
                continue

            industries = [ind["title"] for ind in ch.get("industries", [])]
            hackathon_data = {
                "external_id": ch["id"],
                "organization_id": ch["organization"]["id"],
                "organization_name": ch["organization"]["name"],
                "organization_slug": ch["organization"]["slug"],
                "slug": ch.get("slug", ""),
                "name": clean_text(ch.get("name", "")),
                "industries": industries,
                "processed": False,
            }
            res = (
                supabase.table("hackathons")
                .upsert(hackathon_data, on_conflict=["external_id"])
                .execute()
            )
            hid = res.data[0]["id"]
            cprint(f"✔ Saved hackathon '{hackathon_data['name']}' (ID {hid})", "green")

            projects = fetch_projects_for_challenge(ch["id"], cookies)
            records = [
                {
                    "external_id": p["id"],
                    "hackathon_id": hid,
                    "title": clean_text(p.get("name", "")),
                    "description": clean_text(p.get("description", "")),
                    "tags": [],
                    "url": f"{base_url}/project/{p['id']}",
                    "processed": False,
                }
                for p in projects
            ]

            if records:
                supabase.table("projects").upsert(
                    records, on_conflict=["external_id"]
                ).execute()
                cprint(f"   ↳ {len(records)} projects inserted", "cyan")


def load_hackathons() -> None:
    """Scrape the overview page of each unprocessed hackathon, then mark processed."""
    rows = (
        supabase.table("hackathons")
        .select("id, slug, organization_slug")
        .eq("processed", False)
        .execute()
        .data
        or []
    )
    for h in rows:
        hid, slug, org_slug = h["id"], h["slug"], h["organization_slug"]
        url = f"{base_url}/{org_slug}/hackathons/{slug}"
        driver.get(url)
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        div = soup.find("div", class_="html-editor-body")
        desc = clean_text(div.get_text(separator="\n")) if div else ""
        supabase.table("hackathons").update(
            {"url": url, "description": desc, "processed": True}
        ).eq("id", hid).execute()
        cprint(f"[✔] Updated hackathon '{slug}' (ID {hid})", "green")


def load_projects() -> None:
    """
    Fetch all unprocessed projects from the DB along with their parent hackathon’s
    slug and organization_slug, build the correct project URL, scrape each project,
    update its fields and mark it processed.
    """
    # 1) Fetch projects + related hackathon info in one call
    resp = (
        supabase.table("projects")
        .select(
            """
            id,
            external_id,
            processed,
            hackathon: hackathon_id (
                slug,
                organization_slug
            )
        """
        )
        .eq("processed", False)
        .execute()
    )
    rows = resp.data or []

    if not rows:
        cprint("No unprocessed projects found.", "yellow")
        return

    for row in rows:
        project_id = row["id"]
        external_id = row["external_id"]
        hackathon_info = row.get("hackathon", {})
        hack_slug = hackathon_info.get("slug")
        org_slug = hackathon_info.get("organization_slug")

        if not (hack_slug and org_slug and external_id):
            cprint(f"[✖] Missing data for project ID {project_id}", "red")
            continue

        # 2) Build the project URL
        project_url = (
            f"{base_url}/{org_slug}/hackathons/{hack_slug}/projects/{external_id}/idea"
        )
        cprint(f"→ Scraping {project_url}", "blue")

        # 3) Open and wait for content
        driver.get(project_url)
        time.sleep(2)

        # 4) Extract title, description, tags
        soup = BeautifulSoup(driver.page_source, "html.parser")

        title_tag = soup.find("h1")
        title = clean_text(title_tag.get_text()) if title_tag else ""

        desc_div = soup.find("div", class_="html-editor-body")
        description = clean_text(desc_div.get_text(separator="\n")) if desc_div else ""

        tags = []
        for ul in soup.find_all("ul", class_="tags"):
            tags.extend(clean_text(span.get_text()) for span in ul.find_all("span"))

        # 5) Update record and mark processed
        try:
            supabase.table("projects").update(
                {
                    "title": title,
                    "description": description,
                    "tags": sorted(set(tags)),
                    "processed": True,
                }
            ).eq("id", project_id).execute()
            cprint(f"✔ Updated project ID {project_id}", "cyan")
        except Exception as e:
            cprint(f"[✖] Failed to update project ID {project_id}: {e}", "red")

        time.sleep(1)  # throttle requests


def scrape_and_update_project(project_id: int, project_url: str) -> None:
    """Helper: open project page, extract fields, update DB + mark processed."""
    driver.get(project_url)
    time.sleep(2)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    title_tag = soup.find("h1")
    title = clean_text(title_tag.get_text()) if title_tag else ""
    div = soup.find("div", class_="html-editor-body")
    desc = clean_text(div.get_text(separator="\n")) if div else ""
    tags = [
        clean_text(span.get_text())
        for ul in soup.find_all("ul", class_="tags")
        for span in ul.find_all("span")
    ]

    supabase.table("projects").update(
        {
            "title": title,
            "description": desc,
            "tags": sorted(set(tags)),
            "processed": True,
        }
    ).eq("id", project_id).execute()
    cprint(f"✔ Updated project ID {project_id}", "cyan")


if __name__ == "__main__":
    try:
        cprint("🚀 Starting loader...\n", "cyan", attrs=["bold"])
        cookies = "PUT_REAL_COOKIES_HERE"
        # load_all_data(cookies)
        # load_hackathons()
        load_projects()
    finally:
        driver.quit()
        cprint("\n🧹 Browser session closed", "white")
