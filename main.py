import os
import time
import re
from typing import Optional, Dict, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from supabase import create_client, Client
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

base_url = 'https://taikai.network'
list_url = f"{base_url}/en/hackathons"

emoji_pattern = re.compile("[\U00010000-\U0010ffff]", flags=re.UNICODE)


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = emoji_pattern.sub('', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def wait_for_element(by: By, value: str, timeout: int = 10) -> None:
    time.sleep(2)  # Allow time for the page to load completely
    # WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))


def save_hackathon_data(slug: str, data: Dict[str, str]) -> Optional[int]:
    cleaned_data = {
        "slug": slug,
        "url": clean_text(data.get("url")),
        "description": clean_text(data.get("description")),
        "processed": False
    }
    response = supabase.table("hackathons").upsert(cleaned_data, on_conflict=["slug"]).execute()
    if response.data and isinstance(response.data, list) and "id" in response.data[0]:
        hackathon_id = response.data[0]["id"]
        cprint(f"[✔] Hackathon '{slug}' saved with ID {hackathon_id}", "green")
        return hackathon_id
    else:
        cprint(f"[✖] Failed to save or retrieve hackathon ID for '{slug}'", "red")
        return None


def mark_hackathon_processed(hackathon_id: int) -> None:
    try:
        supabase.table("hackathons").update({"processed": True}).eq("id", hackathon_id).execute()
        cprint(f"   ↳ Hackathon ID {hackathon_id} marked as processed", "green")
    except Exception as e:
        cprint(f"[✖] Failed to mark hackathon as processed: {e}", "red")


def save_project_data(hackathon_id: int, project_data: Dict[str, object]) -> None:
    try:
        cleaned_data = {
            "hackathon_id": hackathon_id,
            "title": clean_text(project_data.get("title", "")),
            "description": clean_text(project_data.get("description", "")),
            "tags": sorted(set(clean_text(tag) for tag in project_data.get("tags", []))),  # type: ignore
            "url": clean_text(project_data.get("url", ""))
        }
        supabase.table("projects").insert(cleaned_data).execute()
        cprint(f"   ↳ Project '{cleaned_data['title'][:60]}' saved", "cyan")
    except Exception as e:
        cprint(f"[✖] Failed to save project: {e}", "red")


def extract_project_data(hackathon_id: int) -> None:
    # wait_for_element(By.CLASS_NAME, 'html-editor-body')
    time.sleep(2)  # Allow time for the page to load completely
    soup = BeautifulSoup(driver.page_source, 'html.parser')

    title = ""
    title_container = soup.find('div', class_='iwSID')
    if title_container:
        h1 = title_container.find('h1')
        if h1:
            title = h1.get_text(strip=True)

    description = ""
    body_div = soup.find('div', class_='html-editor-body')
    if body_div:
        description = body_div.get_text(strip=True, separator='\n')

    tags: List[str] = []
    for ul in soup.find_all('ul', class_='tags'):
        tags += [span.get_text(strip=True) for span in ul.find_all('span')]

    save_project_data(hackathon_id, {
        "title": title,
        "description": description,
        "tags": tags,
        "url": driver.current_url
    })


def extract_data_from_hackathon(hackathon_id: int) -> None:
    while True:
        wait_for_element(By.CSS_SELECTOR, 'div.gFHDc')
        # time.sleep(2)  # Allow time for the page to load completely
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        project_divs = soup.select('div.gFHDc')
        cprint(f"   → Found {len(project_divs)} projects on page", "yellow")

        for div in project_divs:
            a_tag = div.find('a')
            if not a_tag or not a_tag.get('href'):
                continue
            project_url = urljoin(driver.current_url, a_tag['href'])
            driver.get(project_url)
            extract_project_data(hackathon_id)
            driver.back()
            wait_for_element(By.CSS_SELECTOR, 'div.gFHDc')

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        next_btn = soup.select_one('ul.pagination li.next a')
        if not next_btn or next_btn.get('aria-disabled') == 'true':
            cprint("   ↳ No more project pages", "magenta")
            break

        try:
            driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, 'ul.pagination li.next a'))
            cprint("   ↪ Moving to next project page...", "blue")
            time.sleep(3)
        except Exception as e:
            cprint(f"[✖] Pagination failed: {e}", "red")
            break


def navigate_to_hackathon(a_tag: Tag) -> None:
    href = a_tag.get('href')
    if not href:
        return

    slug = href.rstrip('/').split('/')[-1]
    full_url = urljoin(base_url, href)
    driver.get(full_url)
    wait_for_element(By.CLASS_NAME, 'menu')

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    menu = soup.find('ul', class_='menu')
    if not menu:
        return

    hackathon_description = ""
    for li in menu.find_all('li'):
        a = li.find('a')
        if a and a.text.strip() == 'Overview':
            driver.get(urljoin(base_url, a['href']))
            wait_for_element(By.CLASS_NAME, 'html-editor-body')
            overview_soup = BeautifulSoup(driver.page_source, 'html.parser')
            div = overview_soup.find('div', class_='html-editor-body')
            if div:
                hackathon_description = div.get_text(strip=True, separator='\n')
            break

    hackathon_id = save_hackathon_data(slug, {
        "url": full_url,
        "description": hackathon_description
    })
    if not hackathon_id:
        return

    for li in menu.find_all('li'):
        a = li.find('a')
        if a and a.text.strip() == 'Projects':
            driver.get(urljoin(base_url, a['href']))
            # wait_for_element(By.CSS_SELECTOR, 'div.gFHDc')
            time.sleep(2)  # Allow time for the page to load completely
            extract_data_from_hackathon(hackathon_id)
            mark_hackathon_processed(hackathon_id)
            break


def scrape() -> None:
    driver.get(list_url)
    wait_for_element(By.CSS_SELECTOR, 'div.jIFIob')
    page = 1

    while True:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        hackathon_divs = soup.select('div.jIFIob')
        cprint(f"\n🌍 Page {page}: Found {len(hackathon_divs)} hackathons", "blue", attrs=["bold"])

        for div in hackathon_divs:
            status_div = div.find('div', class_='hnwMbs')
            if not status_div or status_div.get_text(strip=True) != "Finished":
                continue

            a_tag = div.find('a')
            if not a_tag:
                continue

            href = a_tag.get('href')
            slug = href.rstrip('/').split('/')[-1]

            # 🔎 Check DB for existing hackathon
            result = supabase.table("hackathons").select("id, processed").eq("slug", slug).execute()
            if result.data:
                existing = result.data[0]
                if existing.get("processed") is True:
                    cprint(f"⏭ Skipping already processed hackathon '{slug}'", "white")
                    continue
                else:
                    hackathon_id = existing["id"]
                    cprint(f"🧹 Removing incomplete projects for '{slug}'", "yellow")
                    supabase.table("projects").delete().eq("hackathon_id", hackathon_id).execute()

            navigate_to_hackathon(a_tag)

        next_button = soup.select_one('ul.pagination li.next a')
        if not next_button or next_button.get('aria-disabled') == 'true':
            cprint("\n✅ Finished all hackathon pages", "green", attrs=["bold"])
            break
        try:
            driver.execute_script("arguments[0].click();", driver.find_element(By.CSS_SELECTOR, 'ul.pagination li.next a'))
            page += 1
            time.sleep(3)
        except Exception as e:
            cprint(f"[✖] Failed to click next hackathon page: {e}", "red")
            break


if __name__ == '__main__':
    try:
        cprint("🚀 Starting hackathon scraper...\n", "cyan", attrs=["bold"])
        scrape()
    finally:
        driver.quit()
        cprint("\n🧹 Browser session closed", "white", attrs=["dark"])
